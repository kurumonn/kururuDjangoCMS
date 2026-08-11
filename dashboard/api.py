"""自動保存 API。

編集中の内容を一定間隔でサーバーへ送り、ブラウザーが落ちても失わないようにする。

この口は「ログイン中の利用者が、自分の記事の内容を、繰り返し書き換える」ため、
次の確認を **すべて** 通さないと開けてはいけない。

  1. ログインしているか           … 未ログインは 403
  2. CSRF トークンが正しいか      … Django のミドルウェアが検証する
  3. その記事を編集してよい人か   … 他人の下書きを書き換えられては困る
  4. 送信サイズが妥当か           … 巨大な JSON でメモリを奪われない
  5. 保存間隔が守られているか     … 毎秒叩かれると DB が持たない
  6. 他の人が先に保存していないか … 上書きで相手の編集を消さない

6 は「楽観ロック」と呼ばれる考え方。
編集画面を開いた時刻の updated_at を一緒に送ってもらい、
サーバー側の updated_at と食い違っていたら保存を断る。
"""

from __future__ import annotations

import json

from django.http import JsonResponse
from django.utils import timezone
from django.views.generic import View

from blog.blocks import validate_blocks
from blog.models import Article
from comments.models import hash_ip
from core.ratelimit import check_rate_limit, client_ip

# 自動保存の上限: 1分間に12回（＝5秒に1回まで）。
AUTOSAVE_LIMIT = 12
AUTOSAVE_WINDOW_SECONDS = 60

# 1回の送信で受け取る JSON の上限（バイト）。
MAX_PAYLOAD_BYTES = 512 * 1024  # 512 KiB


def _error(message: str, status: int, **extra) -> JsonResponse:
    return JsonResponse({"ok": False, "error": message, **extra}, status=status)


class AutosaveView(View):
    """記事の下書きを自動保存する。"""

    def post(self, request, pk):
        if not request.user.is_authenticated:
            # 401 ではなく 403。ログイン画面の HTML を返しても
            # 呼び出し側の JavaScript は解釈できない。
            return _error("ログインしてください。", 403)

        # --- 4. サイズ ---------------------------------------------------
        # request.body を読む前に Content-Length で門前払いする。
        try:
            content_length = int(request.META.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return _error("Content-Length が不正です。", 400)
        if content_length < 0:
            return _error("Content-Length が不正です。", 400)
        if content_length > MAX_PAYLOAD_BYTES:
            return _error("内容が大きすぎます。", 413)

        try:
            payload = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return _error("JSON として読み取れませんでした。", 400)
        if not isinstance(payload, dict):
            return _error("JSON はオブジェクトで送ってください。", 400)

        article = Article.objects.filter(pk=pk).first()
        if article is None:
            return _error("記事が見つかりません。", 404)

        # --- 3. 権限 -----------------------------------------------------
        # 画面側と同じ判定関数を使う。
        # ここで独自の条件を書くと、画面では編集できるのに
        # 自動保存だけ 403 になる、といった食い違いが起きる。
        from blog.views import _can_edit

        if not _can_edit(request.user, article):
            # 存在は知られているので 403 でよい（pk は本人が持っている前提）。
            return _error("この記事を編集する権限がありません。", 403)

        # --- 5. レート制限 -----------------------------------------------
        result = check_rate_limit(
            f"autosave:{request.user.pk}:{pk}",
            limit=AUTOSAVE_LIMIT,
            window_seconds=AUTOSAVE_WINDOW_SECONDS,
        )
        if not result.allowed:
            return _error(
                "自動保存の間隔が短すぎます。", 429, retry_after=result.retry_after
            )

        # --- 6. 競合の検出 -----------------------------------------------
        # 編集画面を開いた時点の版番号を送ってもらい、
        # サーバー側の版番号と食い違っていたら保存を断る。
        #
        # 時刻（updated_at）で比較しない理由はモデル側のコメントを参照。
        # 一言でいうと、丸め差を吸収するための「許容秒数」が、
        # そのまま同時編集を見逃す穴になるため。
        client_version = payload.get("version")
        if not isinstance(client_version, int):
            return _error("version を送ってください。", 400)
        if client_version != article.version:
            return _error(
                "他の場所でこの記事が更新されています。"
                "ページを再読み込みしてから編集してください。",
                409,
                server_version=article.version,
            )

        # --- 内容の検証 ---------------------------------------------------
        title = (payload.get("title") or "").strip()[:200]
        try:
            blocks = validate_blocks(payload.get("blocks"))
        except Exception as exc:  # ValidationError
            message = getattr(exc, "messages", [str(exc)])[0]
            return _error(message, 400)

        # 自動保存は「下書きの内容」だけを書き換える。
        # 公開状態や公開日時は、明示的な操作でしか変えない。
        if title:
            article.title = title
        article.blocks = blocks
        article.save(update_fields=["title", "blocks", "body", "updated_at"])

        return JsonResponse(
            {
                "ok": True,
                # 呼び出し側はこの値を控えて次回に送る。
                # 控え忘れると、2回目の保存が必ず 409 になる。
                "version": article.version,
                "updated_at": article.updated_at.isoformat(),
                "saved_at": timezone.now().isoformat(),
                "block_count": len(blocks),
            }
        )
