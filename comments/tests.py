"""4日目: コメントのテスト。"""

from __future__ import annotations

import time

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from blog.models import Article
from blog.tests.factories import create_article, create_category, create_user

from .forms import MIN_FILL_SECONDS
from .models import Comment, hash_ip

PASSWORD = "test-pass-phrase-1234"  # pragma: allowlist secret


def payload(**overrides):
    """正常に通る投稿内容を作る。

    rendered_at は「MIN_FILL_SECONDS より前にフォームが描画された」状態にする。
    """
    data = {
        "name": "訪問者",
        "email": "",
        "body": "参考になりました。",
        "website": "",
        "rendered_at": int(time.time()) - (MIN_FILL_SECONDS + 1),
    }
    data.update(overrides)
    return data


class CommentVisibilityTests(TestCase):
    def setUp(self):
        self.article = create_article(title="コメント対象", category=create_category())

    def test_unapproved_comment_is_hidden(self):
        Comment.objects.create(
            article=self.article, name="スパム", body="見えてはいけない本文"
        )
        response = self.client.get(self.article.get_absolute_url())
        self.assertNotContains(response, "見えてはいけない本文")

    def test_approved_comment_is_shown(self):
        Comment.objects.create(
            article=self.article,
            name="読者",
            body="表示されるべき本文",
            is_approved=True,
        )
        response = self.client.get(self.article.get_absolute_url())
        self.assertContains(response, "表示されるべき本文")

    def test_spam_comment_is_hidden_even_if_approved(self):
        Comment.objects.create(
            article=self.article,
            name="スパム",
            body="スパム本文",
            is_approved=True,
            is_spam=True,
        )
        response = self.client.get(self.article.get_absolute_url())
        self.assertNotContains(response, "スパム本文")

    def test_comment_body_is_escaped(self):
        Comment.objects.create(
            article=self.article,
            name="攻撃者",
            body="<script>alert('xss')</script>",
            is_approved=True,
        )
        response = self.client.get(self.article.get_absolute_url())
        self.assertNotContains(response, "<script>alert('xss')</script>", html=False)
        self.assertContains(response, "&lt;script&gt;")

    def test_display_name_is_escaped(self):
        Comment.objects.create(
            article=self.article,
            name="<img src=x onerror=alert(1)>",
            body="名前にHTML",
            is_approved=True,
        )
        response = self.client.get(self.article.get_absolute_url())
        self.assertNotContains(response, "<img src=x onerror=alert(1)>", html=False)


class CommentSubmissionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.article = create_article(title="投稿テスト", category=create_category())
        self.url = reverse("comments:create", args=[self.article.slug])

    def test_valid_comment_is_saved_unapproved(self):
        response = self.client.post(self.url, payload())
        self.assertEqual(response.status_code, 302)

        comment = Comment.objects.get()
        self.assertEqual(comment.body, "参考になりました。")
        self.assertFalse(comment.is_approved)

    def test_get_is_not_allowed(self):
        """コメント投稿は状態を変えるので GET では実行できない。"""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)

    def test_honeypot_blocks_bot(self):
        response = self.client.post(self.url, payload(website="http://spam.example"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_too_fast_submission_is_blocked(self):
        response = self.client.post(self.url, payload(rendered_at=int(time.time())))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_missing_rendered_at_is_blocked(self):
        data = payload()
        del data["rendered_at"]
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Comment.objects.count(), 0)

    def test_expired_form_is_blocked(self):
        response = self.client.post(
            self.url, payload(rendered_at=int(time.time()) - 60 * 60 * 24)
        )
        self.assertEqual(response.status_code, 400)

    def test_empty_body_is_blocked(self):
        response = self.client.post(self.url, payload(body=" "))
        self.assertEqual(response.status_code, 400)

    def test_rate_limit_blocks_flooding(self):
        for i in range(5):
            response = self.client.post(self.url, payload(body=f"連投{i}です"))
            self.assertEqual(response.status_code, 302)

        response = self.client.post(self.url, payload(body="6件目"))
        self.assertEqual(response.status_code, 302)  # リダイレクトはするが保存しない
        self.assertEqual(Comment.objects.count(), 5)

    def test_cannot_comment_on_draft_article(self):
        draft = create_article(
            title="下書き記事",
            category=create_category(name="別カテゴリ"),
            status=Article.Status.DRAFT,
        )
        url = reverse("comments:create", args=[draft.slug])
        response = self.client.post(url, payload())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Comment.objects.count(), 0)

    def test_logged_in_user_is_recorded_as_author(self):
        create_user(username="reader")
        self.client.login(username="reader", password=PASSWORD)
        self.client.post(self.url, payload(name="偽名にしたい"))

        comment = Comment.objects.get()
        self.assertIsNotNone(comment.author)
        self.assertEqual(comment.author.username, "reader")
        # 表示名はログインユーザーのものになり、フォームの値では上書きできない。
        self.assertEqual(comment.display_name, "reader")

    def test_ip_is_stored_as_hash_not_raw(self):
        self.client.post(self.url, payload(), REMOTE_ADDR="198.51.100.7")
        comment = Comment.objects.get()

        self.assertNotIn("198.51.100.7", comment.ip_hash)
        self.assertEqual(comment.ip_hash, hash_ip("198.51.100.7"))
        self.assertEqual(len(comment.ip_hash), 64)


class ClientIpTests(TestCase):
    """X-Forwarded-For を無条件に信用しない。

    Nginx の ``proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for``
    は、受け取ったヘッダーの**末尾へ**接続元アドレスを追記する。

        利用者が送ったヘッダー : "1.2.3.4"            ← 偽装できる
        Nginx が書き換えた結果 : "1.2.3.4, 203.0.113.9"
                                            ↑ここだけが信用できる

    つまり信用してよいのは「右から数えて（信頼するプロキシ段数）番目」だけ。
    左端を採用する実装にすると、利用者が好きな IP を名乗れてしまい、
    レート制限も IP 制限も無意味になる。
    """

    def _request(self, forwarded: str, remote_addr: str = "10.0.0.1"):
        from django.test import RequestFactory

        return RequestFactory().get(
            "/", HTTP_X_FORWARDED_FOR=forwarded, REMOTE_ADDR=remote_addr
        )

    def test_forwarded_header_is_ignored_without_trusted_proxy(self):
        """開発既定（プロキシ無し）ではヘッダーを一切見ない。"""
        from core.ratelimit import client_ip

        request = self._request("1.2.3.4", remote_addr="127.0.0.1")
        self.assertEqual(client_ip(request), "127.0.0.1")

    def test_single_proxy_uses_rightmost_entry(self):
        """Nginx 1段構成では、右端が Nginx の見た接続元。"""
        from django.test import override_settings

        from core.ratelimit import client_ip

        request = self._request("1.2.3.4, 203.0.113.9")
        with override_settings(TRUSTED_PROXY_COUNT=1):
            self.assertEqual(client_ip(request), "203.0.113.9")

    def test_spoofed_left_entry_is_not_trusted(self):
        """利用者が偽装した左側の値は採用しない。"""
        from django.test import override_settings

        from core.ratelimit import client_ip

        request = self._request("127.0.0.1, 203.0.113.9")
        with override_settings(TRUSTED_PROXY_COUNT=1):
            self.assertNotEqual(client_ip(request), "127.0.0.1")

    def test_two_proxies_use_second_from_right(self):
        """CDN + Nginx の2段構成では、右から2番目が本当の接続元。"""
        from django.test import override_settings

        from core.ratelimit import client_ip

        # 偽装, 本物の利用者, CDN→Nginx 間のアドレス
        request = self._request("1.2.3.4, 203.0.113.9, 172.16.0.5")
        with override_settings(TRUSTED_PROXY_COUNT=2):
            self.assertEqual(client_ip(request), "203.0.113.9")

    def test_short_header_falls_back_to_remote_addr(self):
        """段数より項目が少ないヘッダーが来ても、壊れずに REMOTE_ADDR へ落ちる。"""
        from django.test import override_settings

        from core.ratelimit import client_ip

        request = self._request("203.0.113.9", remote_addr="10.0.0.1")
        with override_settings(TRUSTED_PROXY_COUNT=2):
            self.assertEqual(client_ip(request), "10.0.0.1")


class IpHashTests(TestCase):
    """IP のハッシュ化について、できること・できないことを固定する。

    これは匿名化ではなく、DB 単体が漏れた場合の緩和策。
    鍵も一緒に漏れれば、IPv4 は約43億通りしかないので総当たりできる。
    """

    def test_hash_is_keyed_hmac_not_plain_sha256(self):
        """鍵なしの SHA-256 では総当たりできてしまうので、HMAC にする。"""
        import hashlib
        import hmac as hmac_module

        from django.conf import settings

        ip = "198.51.100.7"
        key = settings.COMMENT_IP_HASH_KEY.encode("utf-8")

        self.assertEqual(
            hash_ip(ip),
            hmac_module.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest(),
        )
        # 鍵を混ぜない素の SHA-256 とは一致しない。
        self.assertNotEqual(hash_ip(ip), hashlib.sha256(ip.encode()).hexdigest())

    def test_secret_key_rotation_does_not_change_ip_hashes(self):
        """SECRET_KEY を入れ替えても、連投検出の履歴が切れないこと。

        鍵を分けた理由そのもの。SECRET_KEY は漏えい時に必ず
        入れ替えるが、そのとき IP ハッシュまで一斉に変わると
        過去のハッシュと一致しなくなる。
        """
        before = hash_ip("198.51.100.7")

        with self.settings(SECRET_KEY="totally-different-secret-key"):  # pragma: allowlist secret
            self.assertEqual(hash_ip("198.51.100.7"), before)

    def test_changing_the_dedicated_key_changes_the_hash(self):
        before = hash_ip("198.51.100.7")

        with self.settings(COMMENT_IP_HASH_KEY="another-ip-hash-key"):
            self.assertNotEqual(hash_ip("198.51.100.7"), before)

    def test_different_addresses_do_not_collide(self):
        self.assertNotEqual(hash_ip("198.51.100.7"), hash_ip("198.51.100.8"))

    def test_empty_ip_returns_empty_string(self):
        self.assertEqual(hash_ip(None), "")
        self.assertEqual(hash_ip(""), "")
