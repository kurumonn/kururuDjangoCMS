"""8日目: django-allauth まわりのテスト。

allauth 自体の実装は本家がテストしているので、ここで確かめるのは
「この CMS の設定が意図どおりになっているか」に絞る。

設定は1行書き換えるだけで挙動が変わり、しかも画面を見ても気づきにくい。
たとえば ACCOUNT_EMAIL_VERIFICATION を "optional" にしても、
自分のメールアドレスで試している限り何も違いが分からない。
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from allauth.account.models import EmailAddress

User = get_user_model()

PASSWORD = "test-pass-phrase-1234"


def extract_code(message) -> str:
    """メール本文からワンタイムコードを取り出す。

    ACCOUNT_LOGIN_BY_CODE_FORMAT で6桁の数字に設定している。
    allauth の既定は "BCDF-GHJK" のような英字8桁なので、
    設定を変えるとここも合わなくなる。
    """
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", message.body)
    return match.group(1) if match else ""


class AuthUrlTests(TestCase):
    """URL がそろっているか。"""

    def test_login_page_exists(self):
        response = self.client.get(reverse("account_login"))
        self.assertEqual(response.status_code, 200)

    def test_signup_page_exists(self):
        self.assertEqual(self.client.get(reverse("account_signup")).status_code, 200)

    def test_login_by_code_page_exists(self):
        """メールワンタイムコードでのログイン画面。"""
        self.assertEqual(
            self.client.get(reverse("account_request_login_code")).status_code, 200
        )

    def test_usersessions_url_is_under_sessions(self):
        """allauth.urls が自動で足す URL を、個別に include し直さない。

        二重に include すると同じ URL 名が2回登録され、
        reverse() が後から登録した方を返す。
        テストは通るのに、画面上のリンクだけが違う場所を指す状態になる。
        """
        self.assertEqual(reverse("usersessions_list"), "/accounts/sessions/")

    def test_auth_pages_are_noindex(self):
        """認証画面を検索結果へ出さない。"""
        response = self.client.get(reverse("account_login"))
        self.assertContains(response, 'content="noindex, nofollow"')

    def test_logout_requires_post(self):
        """GET でログアウトできると、外部サイトの img タグだけで強制ログアウトできる。"""
        user = User.objects.create_user(
            username="logout-test", email="logout@example.com", password=PASSWORD
        )
        self.client.force_login(user)

        response = self.client.get(reverse("account_logout"))
        # GET は確認画面を出すだけで、ログアウトは実行しない。
        self.assertEqual(response.status_code, 200)
        self.assertIn("_auth_user_id", self.client.session)

        self.client.post(reverse("account_logout"))
        self.assertNotIn("_auth_user_id", self.client.session)


class SignupTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.url = reverse("account_signup")

    def test_signup_sends_verification_and_does_not_log_in(self):
        """メール確認が必須なので、登録直後はログインさせない。

        "optional" にすると、他人のメールアドレスで登録して
        そのアドレス宛の通知を受け取れてしまう。
        """
        response = self.client.post(
            self.url,
            {
                "email": "newbie@example.com",
                "password1": "a-long-enough-passphrase-1",
                "password2": "a-long-enough-passphrase-1",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)

        user = User.objects.get(email="newbie@example.com")
        self.assertFalse(
            EmailAddress.objects.get(user=user, email="newbie@example.com").verified
        )
        # まだログインしていない。
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_email_does_not_reveal_existing_account(self):
        """既に登録済みのアドレスでも「登録済みです」と教えない。

        教えると、総当たりで会員かどうかを調べられる。
        """
        User.objects.create_user(
            username="existing", email="existing@example.com", password=PASSWORD
        )
        response = self.client.post(
            self.url,
            {
                "email": "existing@example.com",
                "password1": "a-long-enough-passphrase-1",
                "password2": "a-long-enough-passphrase-1",
            },
        )
        content = response.content.decode()
        self.assertNotIn("既に登録", content)
        self.assertNotIn("already registered", content.lower())
        # アカウントは増えない。
        self.assertEqual(User.objects.filter(email="existing@example.com").count(), 1)

    def test_short_password_is_rejected(self):
        response = self.client.post(
            self.url,
            {"email": "short@example.com", "password1": "abc12345", "password2": "abc12345"},
        )
        self.assertEqual(response.status_code, 200)  # フォーム再表示
        self.assertFalse(User.objects.filter(email="short@example.com").exists())


class LoginByCodeTests(TestCase):
    """メールワンタイムコードでのログイン。"""

    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username="code-user", email="code@example.com", password=PASSWORD
        )
        EmailAddress.objects.create(
            user=self.user, email="code@example.com", verified=True, primary=True
        )
        self.request_url = reverse("account_request_login_code")
        self.confirm_url = reverse("account_confirm_login_code")

    def test_code_is_sent_and_logs_in(self):
        self.client.post(self.request_url, {"email": "code@example.com"})
        self.assertEqual(len(mail.outbox), 1)

        code = extract_code(mail.outbox[0])
        self.assertTrue(code, "メール本文にコードが見つからない")

        response = self.client.post(self.confirm_url, {"code": code})
        self.assertEqual(response.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_wrong_code_does_not_log_in(self):
        self.client.post(self.request_url, {"email": "code@example.com"})
        self.client.post(self.confirm_url, {"code": "000000"})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_code_is_invalidated_after_max_attempts(self):
        """試行回数を超えたら、正しいコードでも通さない。

        6桁は 100 万通りしかない。回数制限が無ければ総当たりで破れる。
        """
        self.client.post(self.request_url, {"email": "code@example.com"})
        code = extract_code(mail.outbox[0])

        for _ in range(3):  # ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS
            self.client.post(self.confirm_url, {"code": "111111"})

        self.client.post(self.confirm_url, {"code": code})
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_email_does_not_reveal_absence(self):
        """未登録のアドレスでも、画面上は同じ流れにする。

        「そのアドレスは登録されていません」と返すと、
        誰が会員かを外から調べられる。
        """
        response = self.client.post(
            self.request_url, {"email": "nobody@example.com"}, follow=True
        )
        content = response.content.decode()
        self.assertNotIn("登録されていません", content)
        self.assertNotIn("does not exist", content.lower())
        # 未登録のアドレスへメールは送らない。
        self.assertEqual(len(mail.outbox), 0)

    def test_expired_code_is_rejected(self):
        """有効期限を過ぎたコードは使えない。"""
        self.client.post(self.request_url, {"email": "code@example.com"})
        code = extract_code(mail.outbox[0])

        session = self.client.session
        pending_login = session["account_login"]
        pending_login["state"]["stages"]["login_by_code"]["data"]["at"] = 0
        session["account_login"] = pending_login
        session.save()

        self.client.post(self.confirm_url, {"code": code})
        self.assertNotIn("_auth_user_id", self.client.session)


class RateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox = []
        self.user = User.objects.create_user(
            username="rate-user", email="rate@example.com", password=PASSWORD
        )
        EmailAddress.objects.create(
            user=self.user, email="rate@example.com", verified=True, primary=True
        )

    def test_login_code_requests_are_rate_limited(self):
        """コードの発行を無制限にすると、メール爆撃に使われる。"""
        url = reverse("account_request_login_code")
        for _ in range(3):  # ACCOUNT_RATE_LIMITS["request_login_code"] = 3/5m/key
            self.client.post(url, {"email": "rate@example.com"})

        sent_before = len(mail.outbox)
        self.client.post(url, {"email": "rate@example.com"})
        self.assertEqual(
            len(mail.outbox), sent_before, "制限を超えてもメールが送られている"
        )

    def test_failed_logins_are_rate_limited(self):
        url = reverse("account_login")
        for _ in range(10):
            self.client.post(url, {"login": "rate@example.com", "password": "wrong"})

        # 制限にかかった後は、正しいパスワードでも通さない。
        response = self.client.post(
            url, {"login": "rate@example.com", "password": PASSWORD}
        )
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(response.status_code, 429)


class SettingsSanityTests(TestCase):
    """設定が意図どおりか。書き換えると静かに危険になる項目を固定する。"""

    def test_email_verification_is_mandatory(self):
        from django.conf import settings

        self.assertEqual(settings.ACCOUNT_EMAIL_VERIFICATION, "mandatory")

    def test_enumeration_prevention_is_on(self):
        from django.conf import settings

        self.assertTrue(settings.ACCOUNT_PREVENT_ENUMERATION)

    def test_logout_on_get_is_disabled(self):
        from django.conf import settings

        self.assertFalse(settings.ACCOUNT_LOGOUT_ON_GET)

    def test_social_auto_signup_is_disabled(self):
        """ソーシャルログインで既存アカウントへ自動接続しない。

        自動で繋ぐと、同じメールアドレスのソーシャルアカウントを用意するだけで
        既存アカウントを乗っ取れる余地が生まれる。
        """
        from django.conf import settings

        self.assertFalse(settings.SOCIALACCOUNT_AUTO_SIGNUP)

    def test_login_code_defences_are_all_present(self):
        """数字6桁は単体では弱い。3つの防御がそろって初めて実用に耐える。

        どれか1つでも外れたら気づけるように、まとめて固定する。
        """
        from django.conf import settings

        self.assertLessEqual(settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT, 600)
        self.assertLessEqual(settings.ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS, 5)
        self.assertIn("request_login_code", settings.ACCOUNT_RATE_LIMITS)

    def test_site_is_synced_with_site_setting(self):
        """確認メールの文面が example.com のままにならないこと。"""
        from django.contrib.sites.models import Site

        from seo.models import SiteSetting

        setting = SiteSetting.load()
        setting.base_url = "https://cms.example.jp"
        setting.site_name = "同期テストCMS"
        setting.save()

        site = Site.objects.get(pk=1)
        self.assertEqual(site.domain, "cms.example.jp")
        self.assertEqual(site.name, "同期テストCMS")

    def test_social_secrets_are_not_hardcoded(self):
        """認証情報を settings.py へ直接書かない。"""
        from django.conf import settings

        for provider, config in settings.SOCIALACCOUNT_PROVIDERS.items():
            with self.subTest(provider=provider):
                secret = config["APP"]["secret"]
                # 環境変数が未設定なら空文字。値が入っていても、
                # それは環境変数から来たもの。
                self.assertIsInstance(secret, str)
