"""本番設定そのもののテスト。

設定ファイルは実行されないと壊れているか分からない。
そして本番設定は、ふつう本番でしか実行されない。
つまり**壊れていることに気づくのが常に本番**になる。

ここでは production.py を import して、
何が必須で、どこが安全側に倒れているかを固定する。
"""

import importlib
import os
import sys
from contextlib import contextmanager

from django.test import SimpleTestCase

MODULE = "config.settings.production"

# 本番設定を import できる最小の環境変数。
MINIMUM_ENV = {
    "DJANGO_SECRET_KEY": "test-only-not-a-real-key-0123456789-abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
    "DJANGO_COMMENT_IP_HASH_KEY": "test-only-ip-hash-key-9876543210-abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
    "DJANGO_ALLOWED_HOSTS": "cms.example.com",
    "POSTGRES_DB": "kururucms",
    "POSTGRES_APP_USER": "kururucms_app",
    "POSTGRES_APP_PASSWORD": "test-only-password",  # pragma: allowlist secret
    "REDIS_PASSWORD": "test-only-redis-password-9876543210-abcdefghijklmnopqrstuvwxyz",  # pragma: allowlist secret
    "DJANGO_EMAIL_HOST": "smtp.example.com",
}


@contextmanager
def production_settings(**overrides):
    """production.py を、指定した環境変数で読み直す。

    sys.modules から消してから import し直すのが要点。
    Python はモジュールを一度しか実行しないので、
    消さないと2回目以降は最初の環境変数のままになり、
    テストが「通ったことになる」。
    """
    env = {**MINIMUM_ENV, **overrides}
    saved = {key: os.environ.get(key) for key in env}
    saved_module = sys.modules.pop(MODULE, None)
    try:
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield importlib.import_module(MODULE)
    finally:
        sys.modules.pop(MODULE, None)
        if saved_module is not None:
            sys.modules[MODULE] = saved_module
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class MissingSecretsTests(SimpleTestCase):
    """必須の環境変数が無いとき、黙って起動しないこと。"""

    def assert_refuses_without(self, name):
        with self.assertRaises(RuntimeError) as caught:
            with production_settings(**{name: None}):
                pass
        self.assertIn(name, str(caught.exception))

    def test_secret_key_is_required(self):
        self.assert_refuses_without("DJANGO_SECRET_KEY")

    def test_ip_hash_key_is_required(self):
        """IP ハッシュ用の鍵も本番では必須にする。

        未設定でも SECRET_KEY で動いてしまうと、
        「鍵を分けたつもりで分かれていない」状態に気づけない。
        """
        self.assert_refuses_without("DJANGO_COMMENT_IP_HASH_KEY")

    def test_same_secret_and_ip_hash_key_is_rejected(self):
        """用途の違う鍵を同じ値にした本番設定は起動させない。"""
        shared = "test-only-shared-secret-that-is-long-enough-0123456789abcdef"  # pragma: allowlist secret
        with self.assertRaises(RuntimeError) as caught:
            with production_settings(
                DJANGO_SECRET_KEY=shared,
                DJANGO_COMMENT_IP_HASH_KEY=shared,
            ):
                pass
        self.assertIn("DJANGO_COMMENT_IP_HASH_KEY", str(caught.exception))

    def test_short_application_secrets_are_rejected(self):
        for name in ("DJANGO_SECRET_KEY", "DJANGO_COMMENT_IP_HASH_KEY"):
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                with production_settings(**{name: "too-short"}):  # pragma: allowlist secret
                    pass

    def test_allowed_hosts_is_required(self):
        self.assert_refuses_without("DJANGO_ALLOWED_HOSTS")

    def test_database_credentials_are_required(self):
        for name in ("POSTGRES_DB", "POSTGRES_APP_USER", "POSTGRES_APP_PASSWORD"):
            with self.subTest(name=name):
                self.assert_refuses_without(name)

    def test_database_uses_only_application_credentials(self):
        with production_settings() as settings:
            database = settings.DATABASES["default"]
        self.assertEqual(database["USER"], "kururucms_app")

    def test_migration_job_uses_separate_owner_credentials(self):
        with production_settings(
            APP_START_MODE="migrate",
            POSTGRES_MIGRATION_USER="kururucms_migrator",
            POSTGRES_MIGRATION_PASSWORD="test-only-migration-password",
        ) as settings:
            database = settings.DATABASES["default"]
        self.assertEqual(database["USER"], "kururucms_migrator")

    def test_migration_credentials_are_required_for_migration_job(self):
        for name in ("POSTGRES_MIGRATION_USER", "POSTGRES_MIGRATION_PASSWORD"):
            credentials = {
                "POSTGRES_MIGRATION_USER": "kururucms_migrator",
                "POSTGRES_MIGRATION_PASSWORD": "test-only-migration-password",
            }
            credentials[name] = None
            with self.subTest(name=name), self.assertRaises(RuntimeError) as caught:
                with production_settings(
                    APP_START_MODE="migrate",
                    **credentials,
                ):
                    pass
            self.assertIn(name, str(caught.exception))

    def test_email_host_is_required(self):
        self.assert_refuses_without("DJANGO_EMAIL_HOST")

    def test_redis_password_is_required(self):
        self.assert_refuses_without("REDIS_PASSWORD")

    def test_blank_value_counts_as_missing(self):
        """空文字は「設定した」ことにしない。

        .env に `DJANGO_SECRET_KEY=` と書いて満足してしまう事故を防ぐ。
        """
        with self.assertRaises(RuntimeError):
            with production_settings(DJANGO_SECRET_KEY="   "):
                pass


class ProductionHardeningTests(SimpleTestCase):
    """本番で下げてはいけない設定が、実際に上がっていること。"""

    def test_debug_is_off(self):
        with production_settings() as settings:
            self.assertFalse(settings.DEBUG)

    def test_cookies_are_https_only(self):
        with production_settings() as settings:
            self.assertTrue(settings.SESSION_COOKIE_SECURE)
            self.assertTrue(settings.CSRF_COOKIE_SECURE)
            self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)

    def test_https_is_forced(self):
        with production_settings() as settings:
            self.assertTrue(settings.SECURE_SSL_REDIRECT)
            # これが無いと、Nginx の背後で無限リダイレクトになる。
            self.assertEqual(
                settings.SECURE_PROXY_SSL_HEADER, ("HTTP_X_FORWARDED_PROTO", "https")
            )

    def test_hsts_is_enabled_but_starts_short(self):
        """HSTS は有効。ただし既定は短い。

        取り消せない設定なので、既定を1年にしない。
        いきなり1年で出して証明書が切れると、
        HTTP へ戻して復旧することができなくなる。
        """
        with production_settings() as settings:
            self.assertGreater(settings.SECURE_HSTS_SECONDS, 0)
            self.assertLessEqual(settings.SECURE_HSTS_SECONDS, 3600)
            self.assertFalse(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)
            self.assertFalse(settings.SECURE_HSTS_PRELOAD)

    def test_hsts_can_be_raised_by_environment(self):
        with production_settings(
            DJANGO_SECURE_HSTS_SECONDS="31536000",
            DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS="1",
        ) as settings:
            self.assertEqual(settings.SECURE_HSTS_SECONDS, 31536000)
            self.assertTrue(settings.SECURE_HSTS_INCLUDE_SUBDOMAINS)

    def test_webauthn_insecure_origin_cannot_be_enabled(self):
        """パスキーの origin 検査は、環境変数でも緩められないこと。

        開発用の逃げ道を本番設定に残さない。
        """
        with production_settings(DJANGO_MFA_ALLOW_INSECURE_ORIGIN="1") as settings:
            self.assertFalse(settings.MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN)

    def test_database_is_postgresql(self):
        with production_settings() as settings:
            self.assertEqual(
                settings.DATABASES["default"]["ENGINE"],
                "django.db.backends.postgresql",
            )
            self.assertTrue(settings.DATABASES["default"]["CONN_HEALTH_CHECKS"])

    def test_cache_is_shared_between_workers(self):
        """レート制限が正しく効くために、キャッシュは共有でなければならない。

        ローカルメモリだとワーカーごとに別々に数えるので、
        「5回まで」がワーカー4本で実質20回になる。
        """
        with production_settings() as settings:
            backend = settings.CACHES["default"]["BACKEND"]
            self.assertNotIn("locmem", backend)
            self.assertIn("redis", backend)

    def test_email_is_not_printed_to_console(self):
        """本番でコンソールバックエンドのままだと、確認メールが誰にも届かない。"""
        with production_settings() as settings:
            self.assertNotIn("console", settings.EMAIL_BACKEND)
            self.assertIn("smtp", settings.EMAIL_BACKEND)

    def test_csrf_origins_default_to_https_of_allowed_hosts(self):
        with production_settings(
            DJANGO_ALLOWED_HOSTS="cms.example.com,www.example.com"
        ) as settings:
            self.assertEqual(
                settings.CSRF_TRUSTED_ORIGINS,
                ["https://cms.example.com", "https://www.example.com"],
            )

    def test_static_files_get_hashed_names(self):
        with production_settings() as settings:
            self.assertIn(
                "Manifest", settings.STORAGES["staticfiles"]["BACKEND"]
            )

    def test_proxy_count_defaults_to_one(self):
        """Nginx が1段だけ前にいる想定。

        大きすぎると、利用者が偽装した X-Forwarded-For を信じてしまい、
        レート制限を回避される。
        """
        with production_settings() as settings:
            self.assertEqual(settings.TRUSTED_PROXY_COUNT, 1)
            self.assertEqual(settings.ALLAUTH_TRUSTED_PROXY_COUNT, 1)

    def test_wildcard_allowed_host_is_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            with production_settings(DJANGO_ALLOWED_HOSTS="*"):
                pass
        self.assertIn("DJANGO_ALLOWED_HOSTS", str(caught.exception))

    def test_negative_proxy_count_is_rejected(self):
        with self.assertRaises(RuntimeError) as caught:
            with production_settings(DJANGO_TRUSTED_PROXY_COUNT="-1"):
                pass
        self.assertIn("DJANGO_TRUSTED_PROXY_COUNT", str(caught.exception))

    def test_proxy_count_must_match_the_single_nginx_hop(self):
        for count in ("0", "2"):
            with self.subTest(count=count), self.assertRaises(RuntimeError):
                with production_settings(DJANGO_TRUSTED_PROXY_COUNT=count):
                    pass


class SettingsPackageTests(SimpleTestCase):
    """設定の分割そのものについて。"""

    def test_config_settings_cannot_be_used_directly(self):
        """`config.settings` を DJANGO_SETTINGS_MODULE にできないこと。

        どの環境の設定か分からないまま動く状態を作らない。
        __init__.py は空にしてあるので、必要な設定が1つも定義されていない。
        """
        package = importlib.import_module("config.settings")

        self.assertFalse(hasattr(package, "DATABASES"))
        self.assertFalse(hasattr(package, "SECRET_KEY"))
        self.assertFalse(hasattr(package, "INSTALLED_APPS"))

    def test_local_settings_never_reach_production_values(self):
        """開発用設定が、うっかり本番で使えてしまわないこと。"""
        local = importlib.import_module("config.settings.local")

        self.assertTrue(local.DEBUG)
        self.assertIn("django-insecure-", local.SECRET_KEY)
        self.assertIn("sqlite3", local.DATABASES["default"]["ENGINE"])
        # 開発用設定に本番のホスト名が混ざっていないこと。
        self.assertEqual(set(local.ALLOWED_HOSTS), {"localhost", "127.0.0.1", "[::1]"})

    def test_test_settings_do_not_change_behaviour(self):
        """テスト用設定は速さだけを変え、アプリの仕様は変えないこと。

        ここで認証の要件を緩めると、テストが通っても
        本番で動く保証にならなくなる。
        """
        test_settings = importlib.import_module("config.settings.test")

        self.assertEqual(test_settings.ACCOUNT_EMAIL_VERIFICATION, "mandatory")
        self.assertTrue(test_settings.ACCOUNT_PREVENT_ENUMERATION)
        self.assertTrue(test_settings.ACCOUNT_REAUTHENTICATION_REQUIRED)
        self.assertEqual(test_settings.ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS, 3)
        # メールは送らずに貯めるだけ。
        self.assertIn("locmem", test_settings.EMAIL_BACKEND)
