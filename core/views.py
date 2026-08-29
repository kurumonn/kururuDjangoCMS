"""運用のための画面（人向けではないもの）。"""

import secrets

from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request):
    """死活監視用のエンドポイント。

    「プロセスが生きているか」ではなく「**仕事ができる状態か**」を返す。
    Gunicorn は起動していてもデータベースへ繋がらなければ、
    利用者から見れば落ちているのと同じだからである。

    そのため、実際に1本クエリを投げて確認する。

    逆に、ここで重い処理をしてはいけない。
    監視は数秒おきに叩かれるので、記事数を数えるような処理を入れると
    それ自体が負荷になる。SELECT 1 で十分。

    キャッシュを無効にしているのは、
    nginx やブラウザーが 200 を覚えてしまうと、
    落ちていることに気づけなくなるため。
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # 例外の中身は返さない。
        # 接続文字列やホスト名が漏れるおそれがあるため、外向きには状態だけ返す。
        # 詳細はログに出る（production.py の LOGGING）。
        return JsonResponse({"status": "error"}, status=503)

    # セッションと認証レート制限は共有キャッシュに依存する。
    # DB だけ正常でも Redis が落ちていれば、安全にログインを受け付けられない。
    cache_key = f"healthz:{secrets.token_hex(12)}"
    cache_value = secrets.token_hex(12)
    try:
        cache.set(cache_key, cache_value, timeout=5)
        if cache.get(cache_key) != cache_value:
            raise RuntimeError("cache round trip failed")
    except Exception:
        return JsonResponse({"status": "error"}, status=503)
    finally:
        try:
            cache.delete(cache_key)
        except Exception:
            pass

    return JsonResponse({"status": "ok"})
