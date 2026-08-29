"""6日目: SEO・OGP・構造化データ・サイトマップ・RSS のテスト。

「未公開のものが漏れていないか」を、出力先ごとに1件ずつ確認する。
詳細ページで隠せていても、サイトマップや RSS に URL が載れば同じことになる。
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from blog.models import Article
from blog.tests.factories import create_article, create_category, create_tag

from .models import SiteSetting


class CacheClearingTestCase(TestCase):
    """各テストの前にキャッシュを捨てる。

    サイトマップと RSS は cache_page で1時間キャッシュされる。
    テスト間でキャッシュが残ると、前のテストが作った記事の一覧が
    次のテストへそのまま返り、原因の分かりにくい失敗になる。
    サイドバーのキャッシュも同様。
    """

    def setUp(self):
        super().setUp()
        cache.clear()


class SiteSettingTests(CacheClearingTestCase):
    def test_load_creates_singleton(self):
        setting = SiteSetting.load()
        self.assertEqual(setting.pk, 1)
        self.assertEqual(SiteSetting.objects.count(), 1)

    def test_saving_another_instance_overwrites_the_same_row(self):
        SiteSetting.load()
        another = SiteSetting(site_name="別サイト")
        another.save()
        self.assertEqual(SiteSetting.objects.count(), 1)
        self.assertEqual(SiteSetting.load().site_name, "別サイト")

    def test_base_url_trailing_slash_is_removed(self):
        setting = SiteSetting.load()
        setting.base_url = "https://example.com/"
        setting.save()
        self.assertEqual(setting.base_url, "https://example.com")

    def test_absolute_url_joins_correctly(self):
        setting = SiteSetting.load()
        setting.base_url = "https://example.com"
        setting.save()
        self.assertEqual(
            setting.absolute_url("/articles/hello/"),
            "https://example.com/articles/hello/",
        )
        # すでに絶対URLならそのまま返す。
        self.assertEqual(
            setting.absolute_url("https://other.example/x"), "https://other.example/x"
        )

    def test_invalid_color_is_rejected(self):
        from django.core.exceptions import ValidationError

        setting = SiteSetting.load()
        setting.accent_color = "red; } body { display:none"
        with self.assertRaises(ValidationError):
            setting.full_clean()

    def test_setting_cannot_be_deleted(self):
        from django.core.exceptions import ValidationError

        setting = SiteSetting.load()
        with self.assertRaises(ValidationError):
            setting.delete()


class SeoFallbackTests(CacheClearingTestCase):
    def setUp(self):
        super().setUp()
        self.category = create_category()

    def test_seo_title_falls_back_to_title(self):
        article = create_article(title="通常のタイトル", category=self.category)
        self.assertEqual(article.display_seo_title, "通常のタイトル")

    def test_seo_title_is_used_when_set(self):
        article = create_article(
            title="通常のタイトル", category=self.category, seo_title="検索向けタイトル"
        )
        self.assertEqual(article.display_seo_title, "検索向けタイトル")

    def test_description_falls_back_to_body(self):
        article = create_article(
            title="説明文テスト",
            category=self.category,
            body="一行目です。\n\n二行目です。",
        )
        # 改行が潰れて1行になる。
        self.assertEqual(article.display_seo_description, "一行目です。 二行目です。")

    def test_long_body_is_truncated(self):
        article = create_article(
            title="長文", category=self.category, body="あ" * 500
        )
        self.assertLessEqual(len(article.display_seo_description), 160)
        self.assertTrue(article.display_seo_description.endswith("…"))


class MetaTagTests(CacheClearingTestCase):
    def setUp(self):
        super().setUp()
        self.category = create_category()
        setting = SiteSetting.load()
        setting.base_url = "https://cms.example.com"
        setting.site_name = "テストCMS"
        setting.save()

    def test_detail_page_has_canonical(self):
        article = create_article(title="canonical テスト", category=self.category)
        response = self.client.get(article.get_absolute_url())
        self.assertContains(
            response,
            f'<link rel="canonical" href="https://cms.example.com{article.get_absolute_url()}">',
            html=False,
        )

    def test_explicit_canonical_url_wins(self):
        article = create_article(
            title="転載記事",
            category=self.category,
            canonical_url="https://original.example/post",
        )
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, "https://original.example/post")

    def test_noindex_article_emits_meta_robots(self):
        article = create_article(
            title="除外記事", category=self.category, noindex=True
        )
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, 'name="robots"')
        self.assertContains(response, "noindex")

    def test_normal_article_has_no_noindex(self):
        article = create_article(title="通常記事", category=self.category)
        response = self.client.get(article.get_absolute_url())
        self.assertNotContains(response, 'content="noindex, nofollow"')

    def test_preview_of_draft_is_noindex(self):
        """未公開のプレビューが検索結果へ載ってはいけない。"""
        from blog.tests.factories import create_editor

        editor = create_editor(username="seo-editor")
        draft = create_article(
            title="下書きプレビュー",
            author=editor,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        from blog.tests.factories import login_staff

        login_staff(self.client, editor)
        response = self.client.get(draft.get_absolute_url())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'content="noindex, nofollow"')

    def test_og_tags_are_absolute(self):
        article = create_article(title="OGテスト", category=self.category)
        response = self.client.get(article.get_absolute_url())
        self.assertContains(response, 'property="og:url"')
        self.assertContains(response, "https://cms.example.com")


class JsonLdTests(CacheClearingTestCase):
    def setUp(self):
        super().setUp()
        self.category = create_category(name="構造化データ")
        setting = SiteSetting.load()
        setting.base_url = "https://cms.example.com"
        setting.save()

    def _extract_json_ld(self, content: str) -> list[dict]:
        """レスポンスから ld+json ブロックを取り出して JSON として読む。"""
        return [
            json.loads(match.group(1))
            for match in re.finditer(
                r'<script\b[^>]*\btype="application/ld\+json"[^>]*>(.*?)</script>',
                content,
                re.DOTALL,
            )
        ]

    def test_article_json_ld_is_valid_json(self):
        article = create_article(title="JSON-LDテスト", category=self.category)
        response = self.client.get(article.get_absolute_url())
        blocks = self._extract_json_ld(response.content.decode())

        types = {block["@type"] for block in blocks}
        self.assertIn("BlogPosting", types)
        self.assertIn("BreadcrumbList", types)

    def test_script_tag_in_title_does_not_break_json_ld(self):
        """タイトルに </script> が入っても、JSON-LD も HTML も壊れない。

        テンプレートへ手書きしていると、ここで確実に壊れる。
        """
        article = create_article(
            title="危険な</script>タイトル",
            category=self.category,
            slug="dangerous-title",
        )
        response = self.client.get(article.get_absolute_url())
        content = response.content.decode()

        blocks = self._extract_json_ld(content)
        posting = next(b for b in blocks if b["@type"] == "BlogPosting")
        # JSON としては元のタイトルが復元できる。
        self.assertEqual(posting["headline"], "危険な</script>タイトル")

    def test_json_ld_contains_absolute_url(self):
        article = create_article(title="URLテスト", category=self.category)
        response = self.client.get(article.get_absolute_url())
        blocks = self._extract_json_ld(response.content.decode())
        posting = next(b for b in blocks if b["@type"] == "BlogPosting")
        self.assertTrue(posting["url"].startswith("https://cms.example.com/"))


class SitemapTests(CacheClearingTestCase):
    def setUp(self):
        super().setUp()
        self.category = create_category(name="サイトマップ")
        self.url = reverse("seo:sitemap")

    def test_published_article_is_listed(self):
        article = create_article(title="載る記事", category=self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, article.get_absolute_url())

    def test_draft_is_not_listed(self):
        draft = create_article(
            title="下書き",
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, draft.get_absolute_url())

    def test_scheduled_article_is_not_listed(self):
        scheduled = create_article(
            title="予約",
            category=self.category,
            published_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, scheduled.get_absolute_url())

    def test_noindex_article_is_not_listed(self):
        """noindex の記事をサイトマップに載せるのは矛盾している。"""
        hidden = create_article(
            title="除外", category=self.category, noindex=True
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, hidden.get_absolute_url())

    def test_empty_category_is_not_listed(self):
        empty = create_category(name="空カテゴリ")
        response = self.client.get(self.url)
        self.assertNotContains(response, empty.get_absolute_url())

    def test_category_with_articles_is_listed(self):
        create_article(title="カテゴリ用", category=self.category)
        response = self.client.get(self.url)
        self.assertContains(response, self.category.get_absolute_url())

    def test_absolute_urls_use_configured_domain(self):
        """サイトマップの絶対URLは、リクエストのホスト名ではなくサイト設定を使う。

        ここが request.get_host() のままだと、内部ホスト名やリバースプロキシ経由の
        アクセスで、canonical URL と別のドメインがサイトマップへ載る。
        """
        setting = SiteSetting.load()
        setting.base_url = "https://cms.example.com"
        setting.save()

        create_article(title="ドメインテスト", category=self.category)
        # testserver ではない別ホストで叩いても、出力は設定どおりになる。
        # ALLOWED_HOSTS へ足さないと Django が 400 を返してしまい、
        # サイトマップの中身を確認する前に終わってしまう。
        with override_settings(ALLOWED_HOSTS=["internal.local", "testserver"]):
            response = self.client.get(self.url, headers={"host": "internal.local"})
        body = response.content.decode()

        self.assertIn("https://cms.example.com/articles/", body)
        self.assertNotIn("internal.local", body)
        self.assertNotIn("testserver", body)

    def test_http_base_url_is_respected(self):
        """base_url が http なら、サイトマップも http で出す。"""
        setting = SiteSetting.load()
        setting.base_url = "http://cms.example.com"
        setting.save()

        create_article(title="httpテスト", category=self.category)
        body = self.client.get(self.url).content.decode()

        self.assertIn("http://cms.example.com/articles/", body)
        self.assertNotIn("https://cms.example.com", body)


class FeedTests(CacheClearingTestCase):
    def setUp(self):
        super().setUp()
        self.category = create_category(name="フィード")
        self.url = reverse("seo:feed")

    def test_feed_lists_published_articles(self):
        create_article(title="配信される記事", category=self.category)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "配信される記事")

    def test_feed_excludes_draft(self):
        create_article(
            title="配信されない下書き",
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, "配信されない下書き")

    def test_feed_excludes_scheduled(self):
        create_article(
            title="配信されない予約",
            category=self.category,
            published_at=timezone.now() + timedelta(days=1),
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, "配信されない予約")

    def test_atom_feed_works(self):
        create_article(title="Atom記事", category=self.category)
        response = self.client.get(reverse("seo:feed_atom"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Atom記事")

    def test_feed_includes_categories_and_tags(self):
        article = create_article(title="分類つき", category=self.category)
        article.tags.add(create_tag(name="フィードタグ"))
        response = self.client.get(self.url)
        self.assertContains(response, "フィードタグ")

    def test_feed_links_use_configured_domain(self):
        """RSS のリンクもサイト設定のドメインで出す。

        RSS は購読者の手元へ配られ、あとから訂正できない。
        内部ホスト名が入ったまま配信すると、リンクが開けない URL として残る。
        """
        setting = SiteSetting.load()
        setting.base_url = "https://cms.example.com"
        setting.save()

        create_article(title="リンクテスト", category=self.category)
        with override_settings(ALLOWED_HOSTS=["internal.local", "testserver"]):
            response = self.client.get(self.url, headers={"host": "internal.local"})
        body = response.content.decode()

        self.assertIn("https://cms.example.com/articles/", body)
        self.assertNotIn("internal.local", body)

    def test_feed_reflects_setting_change_without_restart(self):
        """設定を変えたら、プロセスを再起動しなくてもフィードへ反映される。

        Feed のインスタンスは URLconf 読み込み時に1個だけ作られて使い回される。
        サイト設定をインスタンス属性へキャッシュすると、ここが古いまま返り続ける。
        """
        create_article(title="設定反映テスト", category=self.category)

        setting = SiteSetting.load()
        setting.site_name = "変更前サイト"
        setting.save()
        first = self.client.get(self.url).content.decode()
        self.assertIn("変更前サイト", first)

        setting.site_name = "変更後サイト"
        setting.save()
        cache.clear()  # cache_page の分だけ捨てる
        second = self.client.get(self.url).content.decode()
        self.assertIn("変更後サイト", second)


class RobotsTxtTests(CacheClearingTestCase):
    def test_normal_site_allows_crawling(self):
        response = self.client.get(reverse("seo:robots"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/plain", response["Content-Type"])
        body = response.content.decode()
        self.assertIn("Allow: /", body)
        self.assertIn("Sitemap:", body)
        self.assertIn("Disallow: /search/", body)

    def test_noindex_site_blocks_everything(self):
        setting = SiteSetting.load()
        setting.noindex_site = True
        setting.save()

        body = self.client.get(reverse("seo:robots")).content.decode()
        self.assertIn("Disallow: /", body)
        self.assertNotIn("Allow: /", body)

    def test_noindex_site_adds_meta_robots_to_pages(self):
        """robots.txt だけでは検索結果から消えない。meta も必要。"""
        setting = SiteSetting.load()
        setting.noindex_site = True
        setting.save()

        response = self.client.get(reverse("blog:article_list"))
        self.assertContains(response, 'content="noindex, nofollow"')
