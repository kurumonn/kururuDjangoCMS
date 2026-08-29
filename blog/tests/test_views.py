"""3日目: 一覧・詳細・投稿・編集・削除と権限のテスト。

「動くこと」より先に「見えてはいけないものが見えないこと」を固定する。
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from blog.models import Article

from .factories import (
    create_article,
    create_author,
    create_category,
    create_editor,
    create_staff,
    create_user,
    login_staff,
)

PASSWORD = "test-pass-phrase-1234"


def listed_titles(response) -> set[str]:
    """一覧ビューが実際に返した記事のタイトル。

    HTML 全体へ assertNotContains をかけると、サイドバーの「最新記事」に
    同じタイトルが載っているだけで失敗する。
    「一覧の中身」を確かめたいときは context を見る。
    """
    return {article.title for article in response.context["articles"]}


class ArticleListViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.category = create_category()

    def test_empty_list_does_not_error(self):
        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "まだ公開された記事がありません")

    def test_published_article_is_listed(self):
        create_article(title="公開記事", category=self.category)
        response = self.client.get(reverse("blog:article_list"))
        self.assertIn("公開記事", listed_titles(response))

    def test_draft_is_not_listed(self):
        create_article(
            title="下書き記事", category=self.category, status=Article.Status.DRAFT
        )
        response = self.client.get(reverse("blog:article_list"))
        self.assertNotIn("下書き記事", listed_titles(response))
        # サイドバーも含めたページ全体へ漏れていないことも確認する。
        self.assertNotContains(response, "下書き記事")

    def test_scheduled_article_is_not_listed(self):
        create_article(
            title="予約記事",
            category=self.category,
            published_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(reverse("blog:article_list"))
        self.assertNotIn("予約記事", listed_titles(response))
        self.assertNotContains(response, "予約記事")

    def test_pagination_splits_at_ten(self):
        for i in range(11):
            create_article(title=f"記事{i}", category=self.category)
        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(len(response.context["articles"]), 10)
        self.assertTrue(response.context["is_paginated"])

    def test_list_query_count_does_not_grow_with_articles(self):
        """N+1 が起きていないことを確認する。

        「クエリがちょうど N 回」と固定すると、
        サイドバーやサイト設定を足すたびにテストが壊れて、
        そのつど数字を書き換えるだけの作業になる。

        本当に見たいのは「記事が増えてもクエリが増えないこと」。
        件数を変えて2回測り、同じであることを確認する。
        """
        from django.db import connection, reset_queries
        from django.test import override_settings

        url = reverse("blog:article_list")

        def count_queries() -> int:
            # キャッシュの有無で結果が変わらないよう、毎回捨てる。
            cache.clear()
            reset_queries()
            self.client.get(url)
            return len(connection.queries)

        with override_settings(DEBUG=True):
            for i in range(5):
                create_article(title=f"N+1テストA{i}", category=self.category)

            # 1回目は測らない。サイト設定の行がまだ無く、
            # その場で INSERT される分だけクエリが余計に増えるため。
            count_queries()
            baseline = count_queries()

            for i in range(15):
                create_article(title=f"N+1テストB{i}", category=self.category)
            grown = count_queries()

        self.assertEqual(
            baseline,
            grown,
            f"記事を増やしたらクエリが {baseline} → {grown} に増えた（N+1 の疑い）",
        )


class ArticleDetailViewTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="detail-author")
        self.published = create_article(
            title="公開済み", author=self.author, category=self.category
        )
        self.draft = create_article(
            title="下書き",
            author=self.author,
            category=self.category,
            status=Article.Status.DRAFT,
        )

    def test_published_article_is_visible_to_anonymous(self):
        response = self.client.get(self.published.get_absolute_url())
        self.assertEqual(response.status_code, 200)

    def test_draft_returns_404_to_anonymous(self):
        """403 ではなく 404。存在自体を隠す。"""
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_draft_returns_404_to_other_user(self):
        create_user(username="stranger")
        self.client.login(username="stranger", password=PASSWORD)
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_author_can_preview_own_draft(self):
        login_staff(self.client, self.author)
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_preview"])

    def test_unrelated_staff_cannot_preview_draft(self):
        staff = create_staff(username="detail-staff")
        login_staff(self.client, staff)
        response = self.client.get(self.draft.get_absolute_url())
        self.assertEqual(response.status_code, 404)

    def test_body_is_escaped(self):
        """本文の HTML はエスケープされ、スクリプトとして実行されない。"""
        create_article(
            title="XSS",
            author=self.author,
            category=self.category,
            body="<script>alert(1)</script>",
            slug="xss-test",
        )
        response = self.client.get(reverse("blog:article_detail", args=["xss-test"]))
        self.assertNotContains(response, "<script>alert(1)</script>", html=False)
        self.assertContains(response, "&lt;script&gt;")


class ArticleCreateViewTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.url = reverse("blog:article_create")

    def _payload(self, **overrides):
        # 既定は下書き。5日目で「公開」は独立した権限になったため、
        # 公開状態で投稿するテストは publish 権限を持つ利用者で行う。
        data = {
            "title": "新しい記事",
            "body": "本文",
            "category": self.category.pk,
            "status": Article.Status.DRAFT,
            "published_at": "",
        }
        data.update(overrides)
        return data

    def test_anonymous_is_redirected_to_login(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("account_login"), response.url)

    def test_user_without_permission_gets_403(self):
        create_user(username="nopower")
        self.client.login(username="nopower", password=PASSWORD)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 403)

    def test_author_can_create(self):
        creator = create_author(username="creator")
        login_staff(self.client, creator)
        response = self.client.post(self.url, self._payload())
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Article.objects.filter(title="新しい記事").exists())

    def test_author_cannot_be_spoofed(self):
        """フォームに他人の ID を混ぜても、著者はログイン中のユーザーになる。"""
        victim = create_user(username="victim")
        attacker = create_author(username="attacker")
        login_staff(self.client, attacker)
        self.client.post(self.url, self._payload(author=victim.pk))

        article = Article.objects.get(title="新しい記事")
        self.assertEqual(article.author.username, "attacker")

    def test_published_without_date_gets_current_time(self):
        editor = create_editor(username="dateless-editor")
        login_staff(self.client, editor)
        self.client.post(self.url, self._payload(status=Article.Status.PUBLISHED))

        article = Article.objects.get(title="新しい記事")
        self.assertIsNotNone(article.published_at)
        self.assertTrue(article.is_visible_to_public)


class ArticleUpdateDeleteViewTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.owner = create_author(username="owner")
        self.article = create_article(
            title="所有者の記事", author=self.owner, category=self.category
        )
        self.update_url = reverse("blog:article_update", args=[self.article.slug])
        self.delete_url = reverse("blog:article_delete", args=[self.article.slug])

    def test_owner_can_edit(self):
        login_staff(self.client, self.owner)
        response = self.client.get(self.update_url)
        self.assertEqual(response.status_code, 200)

    def test_other_author_cannot_edit(self):
        """権限は持っているが、他人の記事なので編集できない。"""
        other = create_author(username="other")
        login_staff(self.client, other)
        response = self.client.get(self.update_url)
        self.assertEqual(response.status_code, 403)

    def test_unrelated_staff_cannot_edit_others_article(self):
        staff = create_staff(username="staffer")
        login_staff(self.client, staff)
        response = self.client.get(self.update_url)
        self.assertEqual(response.status_code, 403)

    def test_other_author_cannot_delete(self):
        other = create_author(username="other-del")
        login_staff(self.client, other)
        response = self.client.post(self.delete_url)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_get_does_not_delete(self):
        """GET は確認画面を出すだけで、削除は実行しない。"""
        login_staff(self.client, self.owner)
        response = self.client.get(self.delete_url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Article.objects.filter(pk=self.article.pk).exists())

    def test_post_deletes(self):
        login_staff(self.client, self.owner)
        response = self.client.post(self.delete_url)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Article.objects.filter(pk=self.article.pk).exists())


class CategoryTagListViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.news = create_category(name="ニュース")
        self.tips = create_category(name="ヒント")
        create_article(title="ニュース記事", category=self.news)
        create_article(title="ヒント記事", category=self.tips)

    def test_category_page_filters_articles(self):
        response = self.client.get(self.news.get_absolute_url())
        titles = listed_titles(response)
        self.assertIn("ニュース記事", titles)
        self.assertNotIn("ヒント記事", titles)

    def test_unknown_category_returns_404(self):
        response = self.client.get("/categories/does-not-exist/")
        self.assertEqual(response.status_code, 404)
