"""7日目: ダッシュボードと自動保存 API のテスト。

自動保存の口は「ログイン中の利用者が自分の記事を繰り返し書き換える」ため、
通してはいけない条件を1つずつ固定する。
"""

from __future__ import annotations

import json
from unittest.mock import patch
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from blog.models import Article
from blog.tests.factories import (
    create_article,
    create_author,
    create_category,
    create_editor,
    create_staff,
    create_user,
    login_staff,
)
from comments.models import Comment

PASSWORD = "test-pass-phrase-1234"


class DashboardViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = create_category()
        self.author = create_author(username="dash-author")
        self.url = reverse("dashboard:index")

    def test_anonymous_is_redirected(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_authenticated_user_without_cms_permission_gets_403(self):
        create_user(username="dash-reader")
        self.client.login(username="dash-reader", password=PASSWORD)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 403)

    def test_counts_are_correct(self):
        create_article(title="公開1", author=self.author, category=self.category)
        create_article(
            title="下書き1",
            author=self.author,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        create_article(
            title="予約1",
            author=self.author,
            category=self.category,
            published_at=timezone.now() + timedelta(days=1),
        )
        create_article(
            title="レビュー1",
            author=self.author,
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=None,
        )

        login_staff(self.client, self.author)
        counts = self.client.get(self.url).context["counts"]

        self.assertEqual(counts["published"], 1)
        self.assertEqual(counts["draft"], 1)
        self.assertEqual(counts["scheduled"], 1)
        self.assertEqual(counts["review"], 1)

    def test_author_sees_only_own_review_requests(self):
        other = create_author(username="dash-other")
        create_article(
            title="他人のレビュー",
            author=other,
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=None,
        )
        create_article(
            title="自分のレビュー",
            author=self.author,
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=None,
        )

        login_staff(self.client, self.author)
        titles = {a.title for a in self.client.get(self.url).context["review_queue"]}
        self.assertEqual(titles, {"自分のレビュー"})

    def test_author_counts_and_schedule_exclude_other_authors_articles(self):
        other = create_author(username="dash-count-other")
        create_article(
            title="他人の下書き",
            author=other,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        create_article(
            title="他人の予約記事",
            author=other,
            category=self.category,
            published_at=timezone.now() + timedelta(days=1),
        )
        create_article(
            title="自分の下書き",
            author=self.author,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )

        login_staff(self.client, self.author)
        response = self.client.get(self.url)

        self.assertEqual(response.context["counts"]["draft"], 1)
        self.assertEqual(response.context["counts"]["scheduled"], 0)
        self.assertNotContains(response, "他人の予約記事")

    def test_editor_sees_all_review_requests(self):
        editor = create_editor(username="dash-editor")
        create_article(
            title="誰かのレビュー",
            author=self.author,
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=None,
        )

        login_staff(self.client, editor)
        response = self.client.get(self.url)
        titles = {a.title for a in response.context["review_queue"]}
        self.assertIn("誰かのレビュー", titles)
        self.assertTrue(response.context["can_review"])

    def test_pending_comments_need_permission(self):
        article = create_article(title="コメント記事", category=self.category)
        Comment.objects.create(article=article, name="訪問者", body="未承認")

        login_staff(self.client, self.author)
        response = self.client.get(self.url)
        # 権限が無い人にはコメント欄自体を出さない。
        self.assertIsNone(response.context.get("pending_comments"))

        moderator = create_editor(username="dash-mod")
        login_staff(self.client, moderator)
        response = self.client.get(self.url)
        self.assertEqual(len(response.context["pending_comments"]), 1)


class AutosaveApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = create_category()
        self.owner = create_author(username="save-owner")
        self.article = create_article(
            title="自動保存テスト",
            author=self.owner,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        self.url = reverse("dashboard:autosave", args=[self.article.pk])

    def _payload(self, **overrides):
        data = {
            "title": "自動保存された題名",
            "blocks": [{"type": "paragraph", "data": {"text": "自動保存された本文"}}],
            "version": self.article.version,
        }
        data.update(overrides)
        return data

    def _post(self, payload=None, **kwargs):
        return self.client.post(
            self.url,
            data=json.dumps(payload if payload is not None else self._payload()),
            content_type="application/json",
            **kwargs,
        )

    # --- 正常系 ---------------------------------------------------------
    def test_owner_can_autosave(self):
        login_staff(self.client, self.owner)
        response = self._post()
        self.assertEqual(response.status_code, 200)

        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["block_count"], 1)

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "自動保存された題名")
        self.assertIn("自動保存された本文", self.article.body)

    def test_unrelated_staff_cannot_autosave_others_article(self):
        staff = create_staff(username="save-staff")
        login_staff(self.client, staff)
        self.assertEqual(self._post().status_code, 403)

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "自動保存テスト")

    def test_response_version_allows_second_save(self):
        """2回続けて保存できる。

        返された version を使わないと、2回目が必ず 409 になる。
        """
        login_staff(self.client, self.owner)
        first = self._post().json()

        second = self._post(self._payload(version=first["version"]))
        self.assertEqual(second.status_code, 200)

    # --- 認証・権限 -----------------------------------------------------
    def test_anonymous_gets_403_json(self):
        """HTML のログイン画面ではなく JSON を返す。

        呼び出し側は JavaScript なので、HTML を返されても解釈できない。
        """
        response = self._post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(response.json()["ok"])

    def test_other_user_cannot_autosave(self):
        stranger = create_author(username="save-stranger")
        login_staff(self.client, stranger)
        response = self._post()
        self.assertEqual(response.status_code, 403)

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "自動保存テスト")

    def test_owner_without_change_permission_cannot_autosave(self):
        owner = create_user(username="save-revoked-owner")
        article = create_article(
            title="権限剥奪済みの記事",
            author=owner,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        self.client.login(username=owner.username, password=PASSWORD)

        response = self.client.post(
            reverse("dashboard:autosave", args=[article.pk]),
            data=json.dumps(self._payload(version=article.version)),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        article.refresh_from_db()
        self.assertEqual(article.title, "権限剥奪済みの記事")

    def test_published_article_is_not_mutated_by_autosave(self):
        self.article.status = Article.Status.PUBLISHED
        self.article.published_at = timezone.now()
        self.article.save()
        original_title = self.article.title
        login_staff(self.client, self.owner)

        response = self._post(self._payload(version=self.article.version))

        self.assertEqual(response.status_code, 409)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, original_title)
        self.assertEqual(self.article.status, Article.Status.PUBLISHED)

    def test_publish_between_initial_read_and_lock_blocks_autosave(self):
        """承認と競合しても、公開後へ古い本文を書き込まない。"""
        login_staff(self.client, self.owner)
        original_select_for_update = Article.objects.select_for_update

        def publish_then_lock(*args, **kwargs):
            Article.objects.filter(pk=self.article.pk).update(
                status=Article.Status.PUBLISHED,
                published_at=timezone.now(),
            )
            return original_select_for_update(*args, **kwargs)

        with patch.object(
            Article.objects,
            "select_for_update",
            side_effect=publish_then_lock,
        ):
            response = self._post()

        self.assertEqual(response.status_code, 409)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "自動保存テスト")
        self.assertEqual(self.article.status, Article.Status.PUBLISHED)

    def test_unknown_article_returns_404(self):
        login_staff(self.client, self.owner)
        response = self.client.post(
            reverse("dashboard:autosave", args=[999999]),
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_get_is_not_allowed(self):
        login_staff(self.client, self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_is_enforced(self):
        """CSRF を切らない。

        自動保存は状態を変える口なので、外部サイトから叩けてはいけない。
        """
        client = self.client_class(enforce_csrf_checks=True)
        login_staff(client, self.owner)
        response = client.post(
            self.url,
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    # --- 入力検証 -------------------------------------------------------
    def test_broken_json_is_rejected(self):
        login_staff(self.client, self.owner)
        response = self.client.post(
            self.url, data="{not json", content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_invalid_block_is_rejected_with_message(self):
        login_staff(self.client, self.owner)
        response = self._post(
            self._payload(blocks=[{"type": "iframe", "data": {"src": "http://evil"}}])
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("未知のブロック種別", response.json()["error"])

    def test_missing_version_is_rejected(self):
        login_staff(self.client, self.owner)
        payload = self._payload()
        del payload["version"]
        self.assertEqual(self._post(payload).status_code, 400)

    def test_non_integer_version_is_rejected(self):
        login_staff(self.client, self.owner)
        self.assertEqual(self._post(self._payload(version="1")).status_code, 400)

    def test_oversized_payload_is_rejected(self):
        login_staff(self.client, self.owner)
        huge = "あ" * 300_000
        response = self._post(
            self._payload(blocks=[{"type": "paragraph", "data": {"text": huge}}])
        )
        self.assertEqual(response.status_code, 413)

    # --- 競合 -----------------------------------------------------------
    def test_stale_version_is_rejected(self):
        """他の人が先に保存していたら、上書きせず 409 を返す。

        時刻の比較で「1秒の許容」を入れていた頃は、
        同じ秒に起きたこの状況をすり抜けて上書きしてしまっていた。
        """
        login_staff(self.client, self.owner)

        stale_version = self.article.version
        # 別の場所で保存された、という状況を作る。
        self.article.title = "他の場所で保存された題名"
        self.article.save()

        response = self._post(self._payload(version=stale_version))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["server_version"], self.article.version)

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "他の場所で保存された題名")

    # --- レート制限 -----------------------------------------------------
    def test_rate_limit_blocks_flooding(self):
        login_staff(self.client, self.owner)

        version = self.article.version
        for i in range(12):
            response = self._post(self._payload(title=f"連投{i}", version=version))
            self.assertEqual(response.status_code, 200, f"{i} 回目で落ちた")
            version = response.json()["version"]

        response = self._post(self._payload(title="13回目", version=version))
        self.assertEqual(response.status_code, 429)
        self.assertIn("retry_after", response.json())

    # --- 権限の境界 -----------------------------------------------------
    def test_autosave_does_not_change_publish_state(self):
        """自動保存で公開状態を変えられてはいけない。"""
        login_staff(self.client, self.owner)
        self._post(
            self._payload(status="published", published_at=timezone.now().isoformat())
        )

        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Article.Status.DRAFT)
        self.assertIsNone(self.article.published_at)

    def test_autosave_does_not_change_author(self):
        victim = create_user(username="save-victim")
        login_staff(self.client, self.owner)
        self._post(self._payload(author=victim.pk))

        self.article.refresh_from_db()
        self.assertEqual(self.article.author, self.owner)
