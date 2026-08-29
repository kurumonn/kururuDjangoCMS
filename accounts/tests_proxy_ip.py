"""django-allauth のレート制限が使うクライアント IP の回帰テスト。"""

from django.test import RequestFactory, SimpleTestCase, override_settings

from allauth.account.adapter import get_adapter


@override_settings(ALLAUTH_TRUSTED_PROXY_COUNT=1)
class AllauthClientIpTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _client_ip(self, forwarded_for: str) -> str:
        request = self.factory.get(
            "/accounts/login/",
            HTTP_X_FORWARDED_FOR=forwarded_for,
            REMOTE_ADDR="172.20.0.5",
        )
        return get_adapter(request).get_client_ip(request)

    def test_distinct_clients_do_not_share_nginx_container_ip(self):
        self.assertEqual(self._client_ip("198.51.100.10"), "198.51.100.10")
        self.assertEqual(self._client_ip("203.0.113.77"), "203.0.113.77")

    def test_spoofed_leftmost_forwarded_ip_is_ignored(self):
        self.assertEqual(
            self._client_ip("127.0.0.1, 198.51.100.10"),
            "198.51.100.10",
        )
