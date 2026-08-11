"""管理画面へ入る利用者に多要素認証を必須にするミドルウェア。

なぜ必要か:

管理画面は「全記事を書き換えられる」「利用者を作れる」「権限を配れる」場所です。
ここのパスワードが1つ漏れるだけで、サイト全体が乗っ取られます。

一方、記事を書くだけの利用者にまで多要素認証を強制すると、
運用が回らなくなって「じゃあ全員 is_staff にしよう」といった逆流が起きます。
そこで **対象を is_staff だけに絞って** 必須化します。

判定は2段構えにします。

  1. **登録**  … 認証手段を1つ以上登録しているか。
                 登録していなければ設定画面へ送る。
  2. **成立**  … いま使っているセッションが、いくつの要素で成立したか。
                 パスキー1本だけで入ったセッションは、管理者権限の画面では
                 もう1要素を求める。

2 が必要な理由（★これが後から足りないと分かった部分★）:

`MFA_PASSKEY_LOGIN_ENABLED = True` にすると、パスキー1本でログインが完了します。
このとき allauth はログインステージ（2段目の認証）を丸ごと飛ばします
（`allauth/mfa/stages.py` の `did_use_passwordless_login`）。

そして allauth は認証時に `UserVerificationRequirement.PREFERRED` を使います。
PREFERRED は「できれば生体認証や PIN も確認して」という**要望**であって要求ではなく、
fido2 は REQUIRED のときしか UV フラグを検査しません
（`fido2/server.py` の `authenticate_complete`）。

結果、PIN を設定していないセキュリティキーなら、**拾っただけで**
管理画面まで入れてしまいます。登録時に UV を要求していても、
認証時に要求していなければ意味がありません。

「登録済みか」だけを見ていると、この穴に気づけません。
セッションが何で成立したかまで見て、初めて要素の数を数えられます。

設計上の注意:

  * 「MFA 設定ページ自体」を塞いではいけない。塞ぐと設定しに行けなくなる。
  * 再認証の画面とログアウトも通す。塞ぐと詰む。
  * 静的ファイルとメディアは対象外。
  * 1 の判定に「いま多要素認証を通ったか」を使わない。
    使うと、セッションのたびに**設定**を求められる。
    要素の数え直しは 2 で別に行う。
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.utils.http import urlencode

# パスキーとは独立した要素として数えるログイン方法。
#
# ここに載っていない方法は数えない（安全側）。allauth が新しい方法を
# 追加したら、管理者は追加の本人確認を1回求められる。
# 面倒ではあるが、黙って要素が1つに減っているよりはよい。
INDEPENDENT_LOGIN_METHODS = frozenset(
    {
        "password",        # パスワード（知識）
        "password_reset",  # 再設定リンク（メールボックスの所持）
        "code",            # メールのワンタイムコード（同上）
        "socialaccount",   # 外部プロバイダー（そちらで認証済み）
    }
)


class StaffMfaRequiredMiddleware:
    """is_staff の利用者に多要素認証の登録を求める。"""

    def __init__(self, get_response):
        self.get_response = get_response
        # 除外パスは起動時に1回だけ組み立てる。
        # リクエストのたびに reverse() を呼ぶと無駄が増える。
        self._exempt_prefixes = self._build_exempt_prefixes()

    @staticmethod
    def _build_exempt_prefixes() -> tuple[str, ...]:
        prefixes = [
            settings.STATIC_URL,
            settings.MEDIA_URL,
        ]
        # これらを塞ぐと「設定しに行けない」「ログアウトもできない」になる。
        for name in (
            "mfa_index",
            "mfa_activate_totp",
            "mfa_view_recovery_codes",
            "mfa_generate_recovery_codes",
            "mfa_download_recovery_codes",
            "mfa_list_webauthn",
            "mfa_add_webauthn",
            "mfa_reauthenticate",
            "mfa_authenticate",
            "account_logout",
            "account_login",
            "account_email",
            "account_email_verification_sent",
            "account_reauthenticate",
        ):
            try:
                prefixes.append(reverse(name))
            except NoReverseMatch:
                # その機能を入れていない構成もある。
                continue
        return tuple(p for p in prefixes if p)

    def __call__(self, request):
        if self._is_out_of_scope(request):
            return self.get_response(request)

        user = request.user

        if not self._has_authenticator(user):
            messages.warning(
                request,
                "管理者権限のアカウントでは、多要素認証の設定が必要です。"
                "認証アプリかパスキーを登録してください。",
            )
            return redirect("mfa_index")

        if not self._has_independent_factor(request):
            messages.warning(
                request,
                "管理者権限の画面へ進むには、もう一度本人確認が必要です。"
                "パスキーだけでは、その端末を持っていることしか確認できません。",
            )
            target = self._reauthentication_url(user)
            return redirect(f"{target}?{urlencode({'next': request.get_full_path()})}")

        return self.get_response(request)

    def _is_out_of_scope(self, request) -> bool:
        """このリクエストは、そもそも判定の対象外か。"""
        if not getattr(settings, "MFA_REQUIRED_FOR_STAFF", False):
            return True

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.is_staff:
            return True

        return request.path.startswith(self._exempt_prefixes)

    @staticmethod
    def _has_authenticator(user) -> bool:
        """多要素認証の手段を1つ以上登録しているか。

        リカバリコードだけを登録した状態は「設定済み」とみなさない。
        リカバリコードは他の手段を失ったときの控えであって、
        単体で日常的に使うものではないため。
        """
        from allauth.mfa.models import Authenticator

        return Authenticator.objects.filter(user=user).exclude(
            type=Authenticator.Type.RECOVERY_CODES
        ).exists()

    @staticmethod
    def _has_independent_factor(request) -> bool:
        """このセッションが、パスキー以外の要素も通って成立したか。

        allauth はログインの過程で通った方法をセッションへ書き残す
        （`account_authentication_methods`）。その記録を読む。

        パスキー（webauthn）は数えない。認証時に UV を強制できない以上、
        「その認証器を持っている」以上のことを確認できていないため。
        2本目のパスキーでも、同じ鍵をもう一度触っても、要素は増えない。

        記録が空のセッションも通さない。allauth を経由していない
        ログイン（`django.contrib.auth.login()` の直接呼び出しなど）を
        黙って素通りさせないため。締め出してもログインし直せば戻れる。
        """
        from allauth.account.authentication import get_authentication_records
        from allauth.mfa.models import Authenticator

        for record in get_authentication_records(request):
            method = record.get("method")
            if method == "mfa":
                if record.get("type") != Authenticator.Type.WEBAUTHN:
                    return True  # 認証アプリ・リカバリコード
            elif method in INDEPENDENT_LOGIN_METHODS:
                return True
        return False

    @staticmethod
    def _reauthentication_url(user) -> str:
        """追加の本人確認をどこで求めるか。

        既定はパスワード。パスキー単独で入った利用者に足りないのは
        「知っていること」なので、そこを埋めるのが素直。

        パスワードを持たない利用者は、いまの設定では作られない
        （`MFA_PASSKEY_SIGNUP_ENABLED = False`、`SOCIALACCOUNT_AUTO_SIGNUP = False`）。
        それでも分岐を書いておく。パスワード入力欄しか無い画面へ送ると、
        その利用者は永久に先へ進めなくなるため。
        """
        if user.has_usable_password():
            return reverse("account_reauthenticate")

        from allauth.mfa.models import Authenticator

        has_totp = Authenticator.objects.filter(
            user=user, type=Authenticator.Type.TOTP
        ).exists()
        return reverse("mfa_reauthenticate" if has_totp else "mfa_index")
