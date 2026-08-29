"""死活監視エンドポイントのテスト。"""

from unittest import mock

from django.test import TestCase
from django.urls import reverse


class HealthzTests(TestCase):
    def test_returns_ok_when_database_is_reachable(self):
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_returns_503_when_database_is_down(self):
        """データベースが落ちていたら 503 を返すこと。

        ここが 200 のままだと、監視は「正常」と言い続ける。
        プロセスが生きていることと、仕事ができることは別である。
        """
        with mock.patch(
            "core.views.connection.cursor", side_effect=Exception("connection refused")
        ):
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "error")

    def test_returns_503_when_shared_cache_is_down(self):
        """Redis が落ちたらセッションと認証レート制限を保証できない。"""
        with mock.patch(
            "core.views.cache.set", side_effect=Exception("redis unavailable")
        ):
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})

    def test_returns_503_when_shared_cache_does_not_round_trip(self):
        with mock.patch("core.views.cache.set"), mock.patch(
            "core.views.cache.get", return_value=None
        ):
            response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})

    def test_does_not_leak_the_error_detail(self):
        """失敗の中身を外へ出さないこと。

        例外文字列には接続先ホストやユーザー名が入ることがある。
        監視は状態だけ分かればよく、原因はログを見る。
        """
        secret = "postgres://kururucms:hunter2@db:5432/kururucms"
        with mock.patch("core.views.connection.cursor", side_effect=Exception(secret)):
            response = self.client.get(reverse("healthz"))

        body = response.content.decode()
        self.assertNotIn("hunter2", body)
        self.assertNotIn("kururucms", body)

    def test_is_not_cached(self):
        """キャッシュされないこと。

        200 を覚えられてしまうと、落ちても気づけない。
        """
        response = self.client.get(reverse("healthz"))

        self.assertIn("no-cache", response.headers.get("Cache-Control", ""))

    def test_needs_no_login(self):
        """認証を要求しないこと。

        監視側に認証情報を持たせると、そこが新しい漏えい経路になる。
        代わりに、返す内容を状態だけに絞ってある。
        """
        response = self.client.get(reverse("healthz"))

        self.assertEqual(response.status_code, 200)
