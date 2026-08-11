"""テストから「本番と同じ経路で」ログインするためのヘルパー。

`Client.login()` と `Client.force_login()` は
`django.contrib.auth.login()` を直接呼ぶ。速くて便利だが、
**allauth のログインフローを通らない**。

その結果、セッションに `account_authentication_methods` が残らない。
`StaffMfaRequiredMiddleware` はこの記録を読んで「いくつの要素で
成立したセッションか」を数えるので、記録の無いセッションは
管理者向けの画面へ進めない（安全側に倒してある）。

テストだけ近道を使うと、「テストは通るのに実際には弾かれる」
という食い違いが起きる。管理者として振る舞うテストは、
ここの関数を通してログインする。

一般利用者のテストは `Client.login()` のままでよい。
このミドルウェアは is_staff だけを対象にしているため。
"""

from __future__ import annotations

import time

from django.urls import reverse

from allauth.mfa.models import Authenticator


def current_totp_code(authenticator: Authenticator) -> str:
    """認証アプリが「いま」表示するのと同じコードを計算する。

    共有秘密鍵と現在時刻から、サーバーと端末が独立に同じ値を出す
    ——これが TOTP の仕組みそのもの。
    allauth には「コードを生成する」公開関数が無い（検証しかしない）ので、
    内部の hotp_value / format_hotp_value を使って組み立てる。
    """
    from allauth.mfa import app_settings as mfa_settings
    from allauth.mfa.totp.internal import auth as totp_auth
    from allauth.mfa.utils import decrypt

    secret = decrypt(authenticator.data["secret"])
    counter = int(time.time()) // mfa_settings.TOTP_PERIOD
    return totp_auth.format_hotp_value(totp_auth.hotp_value(secret, counter))


def login_through_allauth(client, user, password: str) -> None:
    """ログイン画面から、2段目の認証まで通す。

    TOTP を登録している利用者は、パスワードを送っただけでは
    ログインが完了しない（`mfa_authenticate` へ送られる）。
    登録していなければ1段目で終わる。
    """
    client.post(
        reverse("account_login"),
        {"login": user.email, "password": password},
    )

    totp = Authenticator.objects.filter(
        user=user, type=Authenticator.Type.TOTP
    ).first()
    if totp is not None:
        client.post(reverse("mfa_authenticate"), {"code": current_totp_code(totp)})
