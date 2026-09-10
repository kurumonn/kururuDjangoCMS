"""6日目: SEO・OGP・構造化データ・サイトマップ・RSS のテスト。

「未公開のものが漏れていないか」を、出力先ごとに1件ずつ確認する。
詳細ページで隠せていても、サイトマップや RSS に URL が載れば同じことになる。
"""

from __future__ import annotations

import json
import re
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib.admin.sites import AdminSite
from django.test import RequestFactory
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from blog.models import Article
from blog.tests.factories import (
    create_article,
    create_category,
    create_staff,
    create_tag,
)
from pages.models import Page

from .admin import SiteSettingAdmin
from .checks import read_static
from .contrast import (
    NORMAL_TEXT_CONTRAST,
    contrast_ratio,
    ensure_readable_text,
    readable_foreground,
)
from .models import SiteSetting
from .theme_css import audit_reduced_motion, audit_theme, parse_theme_css
from .themes import THEMES, resolve_theme


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

    def test_all_registered_themes_render_from_static_allowlist(self):
        setting = SiteSetting.load()
        for theme in THEMES:
            with self.subTest(theme=theme.key):
                setting.theme_key = theme.key
                setting.full_clean()
                setting.save()
                response = self.client.get(reverse("blog:article_list"))
                self.assertContains(response, f'data-theme="{theme.key}"')
                self.assertContains(response, theme.css_path)

    def test_unknown_theme_is_rejected(self):
        setting = SiteSetting.load()
        setting.theme_key = "../../evil.css"
        with self.assertRaises(ValidationError):
            setting.full_clean()

    def test_accent_foreground_meets_normal_text_contrast(self):
        for background in ("#2563eb", "#60a5fa", "#d9467b", "#777777"):
            foreground = readable_foreground(background)
            self.assertGreaterEqual(contrast_ratio(background, foreground), 4.5)

    def test_permissionless_staff_cannot_add_missing_singleton(self):
        SiteSetting.objects.all().delete()
        request = RequestFactory().get("/admin/seo/sitesetting/")
        request.user = create_staff(username="theme-viewer")
        model_admin = SiteSettingAdmin(SiteSetting, AdminSite())

        self.assertFalse(model_admin.has_add_permission(request))
        with self.assertRaises(PermissionDenied):
            model_admin.changelist_view(request)
        self.assertEqual(SiteSetting.objects.count(), 0)

    def test_theme_style_uses_csp_nonce_and_accessible_foreground(self):
        setting = SiteSetting.load()
        setting.accent_color = "#60a5fa"
        setting.enable_motion = True
        setting.save()

        response = self.client.get(reverse("blog:article_list"))

        self.assertContains(response, 'data-motion="enabled"')
        self.assertContains(response, 'nonce="')
        self.assertContains(response, "--accent-fg: #000000")


class ThemeColorContractTests(CacheClearingTestCase):
    """テーマが「見えている色」として正しいかを見るテスト。

    以前のテーマテストは data-theme 属性とCSSパスの出力しか見ていなかった。
    そのため、テーマ側が --fg ではなく --text を定義していて
    本文色がまったく反映されていない状態でも全部通っていた。
    ここでは実際の色の値まで踏み込む。
    """

    def _rendered_variables(self, theme_key: str) -> dict[str, str]:
        """base.html が差し込むインラインCSSから変数の値を取り出す。"""
        setting = SiteSetting.load()
        setting.theme_key = theme_key
        setting.full_clean()
        setting.save()
        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{3,6});", body))

    def _theme_variables(self, theme) -> dict[str, str]:
        css = read_static(theme.css_path)
        self.assertIsNotNone(css, f"{theme.css_path} が見つかりません")
        return parse_theme_css(css, theme.key).variables

    def test_every_theme_css_satisfies_the_variable_contract(self):
        for theme in THEMES:
            with self.subTest(theme=theme.key):
                self.assertEqual(audit_theme(theme, read_static(theme.css_path)), [])

    def test_no_theme_declares_the_legacy_text_variable(self):
        """--text は共通CSSが読まない。定義しても本文色は変わらない。"""
        for theme in THEMES:
            with self.subTest(theme=theme.key):
                self.assertNotIn("--text", self._theme_variables(theme))

    def test_body_text_meets_normal_text_contrast_on_every_theme(self):
        for theme in THEMES:
            variables = self._theme_variables(theme)
            for name in ("--fg", "--muted", "--danger", "--success"):
                for background in ("--bg", "--surface"):
                    with self.subTest(theme=theme.key, color=name, on=background):
                        self.assertGreaterEqual(
                            contrast_ratio(variables[name], variables[background]),
                            NORMAL_TEXT_CONTRAST,
                        )

    def test_link_colour_is_readable_on_the_active_theme(self):
        """管理画面の既定アクセント色で、11テーマすべてのリンクが読めること。"""
        for theme in THEMES:
            with self.subTest(theme=theme.key):
                variables = self._rendered_variables(theme.key)
                for background in theme.backgrounds:
                    self.assertGreaterEqual(
                        contrast_ratio(variables["--link"], background),
                        NORMAL_TEXT_CONTRAST,
                    )

    def test_accent_follows_the_theme_not_the_visitors_os(self):
        """暗いテーマでは暗い背景用のアクセント色を使う。

        OS の prefers-color-scheme で切り替えると、
        テーマの背景と噛み合わずリンクが 2.3:1 まで落ちる。
        """
        setting = SiteSetting.load()
        setting.accent_color = "#2563eb"
        setting.accent_color_dark = "#60a5fa"
        setting.save()

        self.assertEqual(self._rendered_variables("midnight")["--accent"], "#60a5fa")
        self.assertEqual(self._rendered_variables("clean")["--accent"], "#2563eb")

    def test_unreadable_accent_is_adjusted_for_links_only(self):
        """読めないアクセント色でも、リンクだけは読める色に寄せる。

        ボタンは選んだ色を背景に敷いたままで、載せる文字色は
        readable_foreground() が黒か白を選ぶので読める。
        """
        setting = SiteSetting.load()
        setting.accent_color = "#ffe100"  # 明るい黄色。白地では 1.2:1 しかない。
        setting.save()

        variables = self._rendered_variables("clean")
        self.assertEqual(variables["--accent"], "#ffe100")
        self.assertNotEqual(variables["--link"], "#ffe100")
        for background in resolve_theme("clean").backgrounds:
            self.assertGreaterEqual(
                contrast_ratio(variables["--link"], background), NORMAL_TEXT_CONTRAST
            )
        self.assertGreaterEqual(
            contrast_ratio(variables["--accent"], variables["--accent-fg"]),
            NORMAL_TEXT_CONTRAST,
        )

    def test_readable_accent_is_used_for_links_unchanged(self):
        setting = SiteSetting.load()
        setting.accent_color = "#2563eb"
        setting.save()
        self.assertEqual(self._rendered_variables("clean")["--link"], "#2563eb")

    def test_invalid_stored_accent_falls_back_instead_of_breaking_every_page(self):
        """save() は full_clean() を呼ばない。壊れた色でサイト全体を落とさない。"""
        setting = SiteSetting.load()
        setting.theme_key = "clean"
        setting.save()
        # update() は save() を通らないので、検証されていない値がそのまま入る。
        SiteSetting.objects.filter(pk=setting.pk).update(accent_color="not-a-colour")
        self.assertEqual(SiteSetting.load().accent_color, "not-a-colour")

        response = self.client.get(reverse("blog:article_list"))
        self.assertEqual(response.status_code, 200)
        variables = dict(
            re.findall(
                r"(--[a-z-]+):\s*(#[0-9a-fA-F]{3,6});", response.content.decode()
            )
        )
        self.assertEqual(variables["--accent"], "#2563eb")
        self.assertGreaterEqual(
            contrast_ratio(variables["--link"], "#f7f8fb"), NORMAL_TEXT_CONTRAST
        )

    def test_rendered_page_does_not_switch_colours_on_prefers_color_scheme(self):
        """明暗はテーマが決める。差し込みCSSに配色の分岐を残さない。"""
        setting = SiteSetting.load()
        setting.theme_key = "midnight"
        setting.save()
        self.assertNotContains(
            self.client.get(reverse("blog:article_list")), "prefers-color-scheme"
        )


class ReducedMotionTests(CacheClearingTestCase):
    """「動きを減らす」設定が本当に効くかを見るテスト。

    停止側のセレクターが動かす側より弱いと、CSSの詳細度で負けて止まらない。
    実装は
        html[data-theme="motion"][data-motion="enabled"] .article  (0,3,1) で動かし
        html[data-theme="motion"] .article                          (0,2,1) で止める
    となっていて、利用者が reduce を選んでもアニメーションが動き続けていた。
    """

    def test_theme_stop_rules_use_the_same_selector_as_the_animation(self):
        for theme in THEMES:
            with self.subTest(theme=theme.key):
                self.assertEqual(audit_reduced_motion(read_static(theme.css_path)), [])

    def test_motion_theme_stops_the_exact_selector_it_animates(self):
        css = read_static("themes/motion.css")
        enabling = 'html[data-theme="motion"][data-motion="enabled"] .article'
        self.assertIn(f"{enabling} {{\n  animation: kururu-theme-enter", css)
        reduce_block = re.search(
            r"@media[^{]*prefers-reduced-motion[^{]*\{(.*?\})\s*\}", css, re.S
        )
        self.assertIsNotNone(reduce_block, "reduce ブロックがありません")
        self.assertIn(enabling, reduce_block.group(1))

    def test_site_css_keeps_the_global_killswitch(self):
        """テーマ側の書き忘れに備えた、共通CSSの !important 付き停止ルール。"""
        css = read_static("css/site.css")
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("animation-duration: 0.01ms !important", css)


class EnsureReadableTextTests(TestCase):
    def test_colour_that_already_passes_is_left_alone(self):
        self.assertEqual(ensure_readable_text("#2563eb", ("#ffffff",)), "#2563eb")

    def test_colour_is_darkened_until_it_passes_on_light_backgrounds(self):
        adjusted = ensure_readable_text("#ffe100", ("#ffffff", "#f7f8fb"))
        for background in ("#ffffff", "#f7f8fb"):
            self.assertGreaterEqual(
                contrast_ratio(adjusted, background), NORMAL_TEXT_CONTRAST
            )

    def test_colour_is_lightened_until_it_passes_on_dark_backgrounds(self):
        adjusted = ensure_readable_text("#1e3a8a", ("#0b1020", "#141b2d"))
        for background in ("#0b1020", "#141b2d"):
            self.assertGreaterEqual(
                contrast_ratio(adjusted, background), NORMAL_TEXT_CONTRAST
            )

    def test_result_is_always_a_six_digit_hex(self):
        for color in ("#000", "#fff", "#ffe100", "#2563eb"):
            with self.subTest(color=color):
                self.assertRegex(
                    ensure_readable_text(color, ("#ffffff", "#f7f8fb")),
                    r"^#[0-9a-f]{6}$",
                )


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

    def test_canonical_rejects_non_http_credentials_and_fragments(self):
        article = create_article(title="canonical validation", category=self.category)
        credential_url = "https://" + "user" + ":" + "credential" + "@example.com/article"
        for value in (
            "ftp://example.com/article",
            credential_url,
            "https://example.com/article#fragment",
        ):
            with self.subTest(value=value):
                article.canonical_url = value
                with self.assertRaises(ValidationError):
                    article.full_clean()

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

    def test_external_canonical_is_used_by_link_and_json_ld(self):
        article = create_article(
            title="外部canonical",
            category=self.category,
            canonical_url="https://original.example/article",
        )
        response = self.client.get(article.get_absolute_url())
        blocks = self._extract_json_ld(response.content.decode())
        posting = next(b for b in blocks if b["@type"] == "BlogPosting")

        self.assertContains(response, 'href="https://original.example/article"')
        self.assertEqual(posting["url"], "https://original.example/article")
        self.assertEqual(
            posting["mainEntityOfPage"]["@id"],
            "https://original.example/article",
        )

    def test_page_metadata_and_json_ld_use_same_canonical(self):
        page = Page.objects.create(
            title="会社概要",
            body="会社概要の説明です。",
            seo_title="会社情報",
            seo_description="会社の事業内容と所在地をご案内します。",
            canonical_url="https://original.example/company",
            status=Page.Status.PUBLISHED,
            published_at=timezone.now(),
        )

        response = self.client.get(page.get_absolute_url())
        blocks = self._extract_json_ld(response.content.decode())
        webpage = next(block for block in blocks if block["@type"] == "WebPage")

        self.assertContains(response, "会社情報 |")
        self.assertContains(response, page.seo_description)
        self.assertContains(response, 'href="https://original.example/company"')
        self.assertEqual(webpage["url"], "https://original.example/company")


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

    def test_external_canonical_article_is_not_listed(self):
        article = create_article(
            title="転載記事",
            category=self.category,
            canonical_url="https://original.example/article",
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, article.get_absolute_url())

    def test_noindex_and_external_canonical_pages_are_not_listed(self):
        hidden = Page.objects.create(
            title="非掲載ページ",
            body="本文",
            status=Page.Status.PUBLISHED,
            published_at=timezone.now(),
            noindex=True,
        )
        elsewhere = Page.objects.create(
            title="転載ページ",
            body="本文",
            status=Page.Status.PUBLISHED,
            published_at=timezone.now(),
            canonical_url="https://original.example/page",
        )
        response = self.client.get(self.url)
        self.assertNotContains(response, hidden.get_absolute_url())
        self.assertNotContains(response, elsewhere.get_absolute_url())

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
