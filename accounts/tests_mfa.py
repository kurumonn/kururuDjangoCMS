"""9日目: TOTP・リカバリコード・パスキーのテスト。

allauth の MFA 実装そのものは本家がテストしている。
ここで固定するのは次の2点。

  1. この CMS の設定が意図どおりか（復旧手段を消していないか等）
  2. 自作した「管理者は多要素認証必須」ミドルウェアの挙動
"""

from __future__ import annotations

import time
from urllib.parse import parse_qs, urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator

from accounts.testing import current_totp_code, login_through_allauth

User = get_user_model()

PASSWORD = "test-pass-phrase-1234"  # pragma: allowlist secret


def make_user(username: str, *, is_staff: bool = False) -> User:
    user = User.objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password=PASSWORD,
        is_staff=is_staff,
    )
    EmailAddress.objects.create(
        user=user, email=user.email, verified=True, primary=True
    )
    return user


def add_totp(user) -> Authenticator:
    """TOTP を登録済みの状態にする。"""
    from allauth.mfa.totp.internal import auth as totp_auth

    secret = totp_auth.generate_totp_secret()
    return totp_auth.TOTP.activate(user, secret).instance


def add_passkey(user, *, name: str = "テスト用パスキー") -> Authenticator:
    """パスキーを登録済みの状態にする。

    実際の credential は作らない。ここで確かめたいのは
    「パスキーが1本登録されている状態でミドルウェアがどう振る舞うか」であって、
    WebAuthn の署名検証そのものは allauth と fido2 の担当だから。
    """
    return Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.WEBAUTHN,
        data={"name": name, "credential": {}},
    )


def force_passkey_only_login(client, user, authenticator: Authenticator) -> None:
    """パスキー単独ログインの直後と同じセッションを作る。

    テストから本物の WebAuthn を通すことはできない（署名する認証器が無い）。
    そこで allauth がセッションへ残す**記録の形**を再現する。
    形が合っていることは PasswordlessRecordShapeTests で
    allauth 自身の判定関数と突き合わせて固定している。
    """
    from allauth.account.internal.flows.login import (
        AUTHENTICATION_METHODS_SESSION_KEY,
    )

    client.force_login(user)
    session = client.session
    session[AUTHENTICATION_METHODS_SESSION_KEY] = [
        {
            "method": "mfa",
            "at": time.time(),
            "id": authenticator.pk,
            "type": "webauthn",
            "passwordless": True,
        }
    ]
    session.save()


def append_authentication_record(client, **record) -> None:
    """セッションの認証記録へ1件足す（再認証を通ったあとの状態を作る）。"""
    from allauth.account.internal.flows.login import (
        AUTHENTICATION_METHODS_SESSION_KEY,
    )

    session = client.session
    records = session.get(AUTHENTICATION_METHODS_SESSION_KEY, [])
    records.append({"at": time.time(), **record})
    session[AUTHENTICATION_METHODS_SESSION_KEY] = records
    session.save()


def login_with_password_and_totp(client, user, authenticator: Authenticator) -> None:
    """パスワード＋TOTP で、最後まで本物のログインを通す。"""
    login_through_allauth(client, user, PASSWORD)


class MfaSettingsTests(TestCase):
    """設定を1行消すと静かに危険になる項目を固定する。"""

    def test_recovery_codes_are_enabled(self):
        """リカバリコードを外すと、端末を失った利用者が復旧できなくなる。"""
        from django.conf import settings

        self.assertIn("recovery_codes", settings.MFA_SUPPORTED_TYPES)

    def test_totp_and_webauthn_are_enabled(self):
        from django.conf import settings

        self.assertIn("totp", settings.MFA_SUPPORTED_TYPES)
        self.assertIn("webauthn", settings.MFA_SUPPORTED_TYPES)

    def test_recovery_codes_are_shown_once(self):
        """後からいつでも見られると、画面を覗かれただけで突破される。"""
        from django.conf import settings

        self.assertTrue(settings.MFA_RECOVERY_CODES_SHOW_ONCE)

    def test_passkey_signup_is_disabled(self):
        """登録時にパスキーだけで作らせない。

        その端末を失った時点で復旧手段が無くなるため、
        まずメールアドレスとパスワードを用意させる。
        """
        from django.conf import settings

        self.assertFalse(settings.MFA_PASSKEY_SIGNUP_ENABLED)

    def test_insecure_origin_is_flagged_by_system_check(self):
        """WebAuthn は HTTPS 前提。本番で緩めたままなら起動時に検出する。

        「テストで確かめる」だけでは不十分。
        テストは開発者が実行しないと動かないが、
        システムチェックは runserver や migrate のたびに必ず走る。
        """
        from django.test import override_settings

        from accounts.checks import check_mfa_settings

        with override_settings(DEBUG=False, MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN=True):
            issues = check_mfa_settings(None)
        self.assertTrue(any(i.id == "accounts.E001" for i in issues))

        with override_settings(DEBUG=False, MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN=False):
            issues = check_mfa_settings(None)
        self.assertFalse(any(i.id == "accounts.E001" for i in issues))

    def test_dev_settings_do_not_trigger_checks(self):
        """開発中は何も言わない。邪魔をしないことも要件のうち。"""
        from django.test import override_settings

        from accounts.checks import check_account_settings, check_mfa_settings

        with override_settings(DEBUG=True):
            self.assertEqual(check_mfa_settings(None), [])
            self.assertEqual(check_account_settings(None), [])

    def test_console_email_backend_is_flagged_in_production(self):
        """本番でメールがコンソール出力のままだと、確認メールが誰にも届かない。"""
        from django.test import override_settings

        from accounts.checks import check_account_settings

        with override_settings(
            DEBUG=False,
            EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend",
        ):
            issues = check_account_settings(None)
        self.assertTrue(any(i.id == "accounts.E002" for i in issues))

    def test_totp_tolerance_is_not_too_wide(self):
        """時計のずれを吸収する幅を広げすぎない。

        広いほど、同時に有効なコードが増えて総当たりが楽になる。
        """
        from django.conf import settings

        self.assertLessEqual(settings.MFA_TOTP_TOLERANCE, 60)


class MfaPagesTests(TestCase):
    def setUp(self):
        # allauth のレート制限はキャッシュに残る。
        # 捨てないと、同じクラスの前のテストのログイン試行が数えられ、
        # 無関係なテストが 429 で落ちる。
        cache.clear()
        self.user = make_user("mfa-user")

    def _login_with_password(self):
        """パスワードを入力して実際にログインする。

        force_login() では「いつ本人確認したか」が記録されない。
        ACCOUNT_REAUTHENTICATION_REQUIRED を有効にしていると、
        多要素認証の設定画面が再認証へリダイレクトしてしまう。
        """
        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )

    def test_mfa_index_is_reachable(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("mfa_index"))
        self.assertEqual(response.status_code, 200)

    def test_totp_activation_requires_recent_authentication(self):
        """設定変更の前に、もう一度本人確認を求める。

        セッションを盗まれても、パスワードを知らなければ
        認証手段を勝手に追加できないようにするための仕組み。
        """
        self.client.force_login(self.user)
        response = self.client.get(reverse("mfa_activate_totp"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("reauthenticate", response.url)

    def test_totp_activation_page_is_reachable_after_login(self):
        self._login_with_password()
        response = self.client.get(reverse("mfa_activate_totp"))
        self.assertEqual(response.status_code, 200)

    def test_mfa_pages_are_noindex(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("mfa_index"))
        self.assertContains(response, 'content="noindex, nofollow"')

    def test_anonymous_cannot_reach_mfa_index(self):
        self.client.logout()
        response = self.client.get(reverse("mfa_index"))
        self.assertEqual(response.status_code, 302)


class TotpLoginTests(TestCase):
    """TOTP を登録すると、パスワードだけではログインが完了しない。"""

    def setUp(self):
        cache.clear()
        self.user = make_user("totp-user")

    def test_password_alone_does_not_complete_login(self):
        add_totp(self.user)

        response = self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 302)
        # 2段目の認証画面へ送られる。
        self.assertIn(reverse("mfa_authenticate"), response.url)

        # まだ本ログインは成立していない。
        dashboard = self.client.get(reverse("dashboard:index"))
        self.assertEqual(dashboard.status_code, 302)

    def test_wrong_totp_code_does_not_log_in(self):
        add_totp(self.user)
        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )

        self.client.post(reverse("mfa_authenticate"), {"code": "000000"})
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_correct_totp_code_completes_login(self):
        authenticator = add_totp(self.user)

        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )

        code = current_totp_code(authenticator)
        response = self.client.post(reverse("mfa_authenticate"), {"code": code})
        self.assertEqual(response.status_code, 302)

        self.assertIn("_auth_user_id", self.client.session)


class RecoveryCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("recovery-user")
        add_totp(self.user)

    def _generate_codes(self) -> list[str]:
        from allauth.mfa.recovery_codes.internal import auth as rc_auth

        authenticator = rc_auth.RecoveryCodes.activate(self.user).instance
        return rc_auth.RecoveryCodes(authenticator).get_unused_codes()

    def test_recovery_code_logs_in(self):
        codes = self._generate_codes()

        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )
        response = self.client.post(reverse("mfa_authenticate"), {"code": codes[0]})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_recovery_code_cannot_be_reused(self):
        """一度使ったリカバリコードは二度と使えない。

        再利用できると、紙を一度覗かれただけで何度でも入られる。
        """
        codes = self._generate_codes()

        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )
        self.client.post(reverse("mfa_authenticate"), {"code": codes[0]})
        self.client.post(reverse("account_logout"))

        # 同じコードでもう一度。
        self.client.post(
            reverse("account_login"),
            {"login": self.user.email, "password": PASSWORD},
        )
        self.client.post(reverse("mfa_authenticate"), {"code": codes[0]})
        self.assertEqual(self.client.get(reverse("dashboard:index")).status_code, 302)

    def test_expected_number_of_codes_is_generated(self):
        from django.conf import settings

        self.assertEqual(len(self._generate_codes()), settings.MFA_RECOVERY_CODE_COUNT)


@override_settings(MFA_REQUIRED_FOR_STAFF=True)
class StaffMfaRequiredMiddlewareTests(TestCase):
    """管理者は多要素認証を登録するまで、他の画面へ進めない。"""

    def setUp(self):
        cache.clear()
        self.staff = make_user("mfa-staff", is_staff=True)
        self.author = make_user("mfa-author")

    def test_staff_without_mfa_is_redirected(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("mfa_index"))

    def test_staff_can_still_reach_mfa_setup(self):
        """設定ページ自体を塞ぐと、設定しに行けなくなる。

        ミドルウェアが 302 を返していないことが要点。
        再認証への 302 は allauth の仕様なので、
        「mfa_index へ差し戻されていない」ことで判定する。
        """
        self.client.post(
            reverse("account_login"),
            {"login": self.staff.email, "password": PASSWORD},
        )
        for name in ("mfa_index", "mfa_activate_totp", "mfa_list_webauthn"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)

    def test_staff_can_still_log_out(self):
        """ログアウトを塞ぐと詰む。"""
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse("account_logout")).status_code, 200)

    def test_staff_with_totp_passes_through(self):
        authenticator = add_totp(self.staff)
        login_with_password_and_totp(self.client, self.staff, authenticator)
        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_recovery_codes_alone_do_not_count(self):
        """リカバリコードは控えであって、日常の認証手段ではない。"""
        from allauth.mfa.recovery_codes.internal import auth as rc_auth

        rc_auth.RecoveryCodes.activate(self.staff)
        self.client.post(
            reverse("account_login"),
            {"login": self.staff.email, "password": PASSWORD},
        )

        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("mfa_index"))

    def test_non_staff_is_not_forced(self):
        """記事を書くだけの利用者にまで強制すると運用が回らない。"""
        self.client.force_login(self.author)
        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_non_staff_publisher_without_mfa_is_redirected(self):
        publisher = make_user("mfa-publisher")
        publisher.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="blog", codename="publish_article"
            )
        )
        self.client.force_login(publisher)

        response = self.client.get(reverse("blog:article_list"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("mfa_index"))

    def test_anonymous_is_not_affected(self):
        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_static_urls_are_not_redirected(self):
        self.client.force_login(self.staff)
        response = self.client.get("/static/css/site.css")
        self.assertNotEqual(response.status_code, 302)

    @override_settings(MFA_REQUIRED_FOR_STAFF=False)
    def test_can_be_disabled(self):
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )


class PasswordlessRecordShapeTests(TestCase):
    """テストで作る「パスキー単独ログイン」の記録が、本物と同じ形であること。

    偽のセッションを組み立ててテストする以上、その形が allauth の実物と
    ずれていたら、テストは何も証明しない。allauth 自身の判定関数に
    同じ記録を読ませて、True が返ることで形を固定する。
    allauth 側が形を変えたら、このテストが落ちて気づける。
    """

    def setUp(self):
        cache.clear()
        self.user = make_user("shape-user")

    def test_allauth_agrees_this_is_a_passwordless_login(self):
        from allauth.mfa.webauthn.internal.flows import did_use_passwordless_login

        authenticator = add_passkey(self.user)
        force_passkey_only_login(self.client, self.user, authenticator)

        request = self.client.get(reverse("blog:article_list")).wsgi_request
        self.assertTrue(did_use_passwordless_login(request))

    def test_a_normal_login_is_not_seen_as_passwordless(self):
        from allauth.mfa.webauthn.internal.flows import did_use_passwordless_login

        authenticator = add_totp(self.user)
        login_with_password_and_totp(self.client, self.user, authenticator)

        request = self.client.get(reverse("blog:article_list")).wsgi_request
        self.assertFalse(did_use_passwordless_login(request))


@override_settings(MFA_REQUIRED_FOR_STAFF=True)
class StaffSessionFactorTests(TestCase):
    """管理者権限の画面は「何要素で成立したセッションか」で判定する。

    パスキー単独ログイン（MFA_PASSKEY_LOGIN_ENABLED = True）は、
    利用者から見れば一瞬で終わる便利な入口だが、
    要素としては「その認証器を持っていること」1つしかない。

    しかも allauth は認証時に UserVerificationRequirement.PREFERRED を使う。
    PREFERRED は「できれば生体認証や PIN を確認して」という要望であって、
    要求ではない。fido2 は REQUIRED のときしか UV フラグを検査しないので、
    PIN を設定していないセキュリティキーなら、拾っただけで通る。

    記事を読むだけならそれで構わない。
    しかし管理画面は全記事を書き換えられる場所なので、
    ここだけは「もう1要素」を求める。
    """

    def setUp(self):
        cache.clear()
        self.staff = make_user("factor-staff", is_staff=True)
        self.author = make_user("factor-author")
        self.passkey = add_passkey(self.staff)

    def test_passkey_only_session_cannot_reach_staff_pages(self):
        force_passkey_only_login(self.client, self.staff, self.passkey)

        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_reauthenticate"), response.url)

    def test_passkey_only_session_can_still_reach_reauthentication(self):
        """追加の本人確認をしに行く先を塞ぐと、詰んで抜けられなくなる。

        `mfa_reauthenticate` は allauth 自身が
        `/accounts/reauthenticate/` へ回すことがある（認証アプリ未登録のとき）。
        302 を禁止するのではなく、**最終的に開けること**を確かめる。
        """
        force_passkey_only_login(self.client, self.staff, self.passkey)

        for name in ("account_reauthenticate", "mfa_reauthenticate", "account_logout"):
            with self.subTest(name=name):
                response = self.client.get(reverse(name), follow=True)
                self.assertEqual(response.status_code, 200)

    def test_the_gate_does_not_bounce_forever(self):
        """締め出した先が、また締め出されないこと。

        追加要素を求める画面自体をミドルウェアが弾くと、
        リダイレクトが往復して利用者は永久に前へ進めない。
        """
        force_passkey_only_login(self.client, self.staff, self.passkey)

        response = self.client.get(reverse("blog:article_list"), follow=True)
        self.assertEqual(response.status_code, 200)
        # 最後に着いた場所が、堂々巡りではなく本人確認の画面であること。
        final_url = response.redirect_chain[-1][0]
        self.assertIn("reauthenticate", final_url)

    def test_the_gate_remembers_where_the_user_was_going(self):
        force_passkey_only_login(self.client, self.staff, self.passkey)

        response = self.client.get(reverse("blog:article_list"))

        query = parse_qs(urlparse(response.url).query)
        self.assertEqual(query["next"], [reverse("blog:article_list")])

    def test_password_reauthentication_unlocks_the_session(self):
        force_passkey_only_login(self.client, self.staff, self.passkey)
        append_authentication_record(
            self.client, method="password", reauthenticated=True
        )

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_totp_counts_as_the_second_factor(self):
        force_passkey_only_login(self.client, self.staff, self.passkey)
        append_authentication_record(
            self.client, method="mfa", type="totp", id=999, reauthenticated=True
        )

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_tapping_a_passkey_again_is_not_a_second_factor(self):
        """同じ種類の要素を2回通しても、要素は1つのまま。

        再認証の画面にはパスキーの選択肢も出る。そこで同じ鍵を
        もう一度触っただけで通ってしまうなら、この対策は形だけになる。
        """
        force_passkey_only_login(self.client, self.staff, self.passkey)
        append_authentication_record(
            self.client,
            method="mfa",
            type="webauthn",
            id=self.passkey.pk,
            reauthenticated=True,
        )

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 302
        )

    def test_a_different_passkey_is_also_not_a_second_factor(self):
        """2本目のパスキーでも、確認できていないものは同じ。"""
        other = add_passkey(self.staff, name="2本目")
        force_passkey_only_login(self.client, self.staff, self.passkey)
        append_authentication_record(
            self.client, method="mfa", type="webauthn", id=other.pk
        )

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 302
        )

    def test_session_without_any_allauth_record_is_blocked(self):
        """allauth を通っていないセッションは通さない（安全側に倒す）。

        force_login() や、将来 django.contrib.auth.login() を直接呼ぶ
        コードが増えたときに、黙って素通りさせないための線引き。
        締め出しても復旧はログインし直すだけなので、緩めるより厳しくする。
        """
        add_totp(self.staff)
        self.client.force_login(self.staff)

        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 302)

    def test_normal_password_and_totp_login_is_unaffected(self):
        authenticator = add_totp(self.staff)
        login_with_password_and_totp(self.client, self.staff, authenticator)

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_non_staff_can_use_a_passkey_alone(self):
        """記事を書くだけの利用者にまで追加要素を求めない。

        パスキー単独ログインを丸ごと無効にするのではなく、
        権限の強い画面だけ条件を上げる、という切り分け。
        """
        passkey = add_passkey(self.author)
        force_passkey_only_login(self.client, self.author, passkey)

        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )

    def test_privileged_non_staff_cannot_use_a_passkey_alone(self):
        publisher = make_user("factor-publisher")
        publisher.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="blog", codename="publish_article"
            )
        )
        passkey = add_passkey(publisher)
        force_passkey_only_login(self.client, publisher, passkey)

        response = self.client.get(reverse("blog:article_list"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_reauthenticate"), response.url)

    @override_settings(MFA_REQUIRED_FOR_STAFF=False)
    def test_gate_follows_the_same_switch(self):
        force_passkey_only_login(self.client, self.staff, self.passkey)
        self.assertEqual(
            self.client.get(reverse("blog:article_list")).status_code, 200
        )


class AdminLoginTests(TestCase):
    """Django 標準の管理画面ログインは、多要素認証を通らない。

    `admin.site.urls` には admin 自身のログイン画面が含まれる。
    これは allauth のログインフローとは別物で、
    ログインステージ（＝2段目の認証）を一切通らない。

    つまり /admin/login/ を開けたままにしていると、
    TOTP もパスキーも登録済みの管理者が、
    **パスワードだけで管理画面へ入れてしまう**。
    多要素認証を必須にした意味が無くなる。
    """

    def setUp(self):
        cache.clear()
        self.staff = make_user("admin-login-staff", is_staff=True)
        self.staff.is_superuser = True
        self.staff.save()
        self.admin_login_url = f"/{settings.ADMIN_URL_PATH}/login/"

    def test_admin_login_page_redirects_to_allauth(self):
        response = self.client.get(self.admin_login_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_password_alone_cannot_enter_the_admin(self):
        add_totp(self.staff)

        self.client.post(
            self.admin_login_url,
            {
                "username": self.staff.email,
                "password": PASSWORD,
                "next": f"/{settings.ADMIN_URL_PATH}/",
            },
        )

        response = self.client.get(f"/{settings.ADMIN_URL_PATH}/")
        self.assertEqual(response.status_code, 302)

    def test_the_redirect_keeps_where_the_user_wanted_to_go(self):
        response = self.client.get(f"{self.admin_login_url}?next=/admin/blog/")
        self.assertIn("next=/admin/blog/", response.url)
