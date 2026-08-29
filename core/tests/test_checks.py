"""運用まわりのシステムチェックのテスト。

チェック自体が壊れていると、「警告が出ていない＝安全」という
誤った安心を与えてしまう。だからチェックにもテストを書く。
"""

from django.test import SimpleTestCase, override_settings

from core.checks import check_hsts_rollout, check_proxy_configuration

PROXY_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")


def ids(issues):
    return [issue.id for issue in issues]


def hints(issues):
    return " ".join(issue.hint or "" for issue in issues)


@override_settings(DEBUG=False)
class HstsRolloutCheckTests(SimpleTestCase):
    @override_settings(DEBUG=True, SECURE_HSTS_SECONDS=0)
    def test_says_nothing_during_development(self):
        """開発中は黙る。開発機は HTTP なので、指摘しても直しようがない。"""
        self.assertEqual(check_hsts_rollout(None), [])

    @override_settings(SECURE_HSTS_SECONDS=0)
    def test_errors_when_hsts_is_disabled(self):
        self.assertEqual(ids(check_hsts_rollout(None)), ["core.E001"])

    @override_settings(
        SECURE_HSTS_SECONDS=3600,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_first_step_points_at_the_second(self):
        issues = check_hsts_rollout(None)

        self.assertEqual(ids(issues), ["core.I001"])
        self.assertIn("604800", hints(issues))

    @override_settings(
        SECURE_HSTS_SECONDS=604800,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_second_step_points_at_one_year(self):
        self.assertIn("31536000", hints(check_hsts_rollout(None)))

    @override_settings(
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_one_year_points_at_subdomains(self):
        self.assertIn("INCLUDE_SUBDOMAINS", hints(check_hsts_rollout(None)))

    @override_settings(
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_subdomains_done_points_at_preload(self):
        self.assertIn("preload", hints(check_hsts_rollout(None)).lower())

    @override_settings(
        SECURE_HSTS_SECONDS=31536000,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=True,
        SECURE_HSTS_PRELOAD=True,
    )
    def test_fully_rolled_out_says_it_is_done(self):
        self.assertIn("完了", hints(check_hsts_rollout(None)))

    @override_settings(
        SECURE_HSTS_SECONDS=3600,
        SECURE_HSTS_INCLUDE_SUBDOMAINS=False,
        SECURE_HSTS_PRELOAD=False,
    )
    def test_progress_report_never_blocks_startup(self):
        """段階の案内は Info であること。

        Warning にすると `check --deploy --fail-level WARNING` が
        起動を止めてしまい、「段階的に上げる」ことができなくなる。
        """
        for issue in check_hsts_rollout(None):
            self.assertFalse(issue.is_serious())


@override_settings(DEBUG=False)
class ProxyCheckTests(SimpleTestCase):
    @override_settings(
        DEBUG=True, SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=None
    )
    def test_says_nothing_during_development(self):
        self.assertEqual(check_proxy_configuration(None), [])

    @override_settings(
        SECURE_SSL_REDIRECT=True, SECURE_PROXY_SSL_HEADER=None, TRUSTED_PROXY_COUNT=1
    )
    def test_detects_the_infinite_redirect_setup(self):
        """SSL リダイレクトだけ有効でヘッダー設定が無い＝無限ループになる組み合わせ。"""
        issues = check_proxy_configuration(None)

        self.assertIn("core.E002", ids(issues))
        self.assertIn("無限ループ", hints(issues))

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=PROXY_HEADER,
        TRUSTED_PROXY_COUNT=0,
    )
    def test_warns_when_forwarded_for_is_ignored_behind_a_proxy(self):
        """プロキシの背後で 0 のままだと、全員が同じ IP に見える。"""
        self.assertIn("core.W001", ids(check_proxy_configuration(None)))

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=PROXY_HEADER,
        TRUSTED_PROXY_COUNT=9,
    )
    def test_warns_when_too_many_proxies_are_trusted(self):
        self.assertIn("core.W002", ids(check_proxy_configuration(None)))

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=PROXY_HEADER,
        TRUSTED_PROXY_COUNT=1,
        ALLAUTH_TRUSTED_PROXY_COUNT=0,
    )
    def test_errors_when_allauth_uses_a_different_proxy_count(self):
        self.assertIn("core.E003", ids(check_proxy_configuration(None)))

    @override_settings(
        SECURE_SSL_REDIRECT=True,
        SECURE_PROXY_SSL_HEADER=PROXY_HEADER,
        TRUSTED_PROXY_COUNT=1,
        ALLAUTH_TRUSTED_PROXY_COUNT=1,
    )
    def test_correct_setup_is_quiet(self):
        self.assertEqual(check_proxy_configuration(None), [])
