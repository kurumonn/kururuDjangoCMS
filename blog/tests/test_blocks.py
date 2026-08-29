"""7日目: ブロックの検証と描画のテスト。"""

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from blog.blocks import MAX_BLOCKS, blocks_to_plain_text, validate_blocks
from blog.models import Article, ReusableBlock
from cms_plugins.models import PluginActivation
from cms_plugins.registry import (
    PluginBlock,
    PluginDefinition,
    clear_registry,
    register_plugin,
)

from .factories import create_article, create_category


class ValidateBlocksTests(TestCase):
    def tearDown(self):
        clear_registry()
        super().tearDown()

    def test_empty_is_allowed(self):
        self.assertEqual(validate_blocks(None), [])
        self.assertEqual(validate_blocks([]), [])

    def test_valid_blocks_are_normalized(self):
        result = validate_blocks(
            [
                {"type": "heading", "data": {"level": 2, "text": "  見出し  "}},
                {"type": "paragraph", "data": {"text": "本文"}},
            ]
        )
        self.assertEqual(result[0]["data"]["text"], "見出し")  # 前後の空白は落ちる
        self.assertEqual(result[1]["type"], "paragraph")

    def test_unknown_type_is_rejected(self):
        """未知の種類を黙って捨てない。

        捨てると「保存できたのに表示されない」という
        原因の分からない状態になる。
        """
        with self.assertRaises(ValidationError) as ctx:
            validate_blocks([{"type": "iframe", "data": {"src": "http://evil"}}])
        self.assertIn("未知のブロック種別", str(ctx.exception))

    def test_not_a_list_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_blocks({"type": "paragraph"})

    def test_too_many_blocks_are_rejected(self):
        blocks = [{"type": "paragraph", "data": {"text": "x"}}] * (MAX_BLOCKS + 1)
        with self.assertRaises(ValidationError):
            validate_blocks(blocks)

    def test_heading_level_is_limited(self):
        """h1 は記事タイトルが使うので、本文では h2〜h4 に限る。"""
        for level in (1, 5, 0, "2"):
            with self.subTest(level=level):
                with self.assertRaises(ValidationError):
                    validate_blocks(
                        [{"type": "heading", "data": {"level": level, "text": "x"}}]
                    )

    def test_empty_text_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_blocks([{"type": "paragraph", "data": {"text": "   "}}])

    def test_cta_rejects_javascript_url(self):
        """javascript: を許すと、リンクを踏ませるだけでスクリプトが動く。"""
        for url in ("javascript:alert(1)", "data:text/html,<script>", "vbscript:x"):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    validate_blocks(
                        [{"type": "cta", "data": {"text": "押す", "url": url}}]
                    )

    def test_cta_accepts_https_and_relative(self):
        result = validate_blocks(
            [{"type": "cta", "data": {"text": "押す", "url": "/articles/"}}]
        )
        self.assertEqual(result[0]["data"]["url"], "/articles/")

    def test_code_language_is_restricted(self):
        """言語名はそのまま CSS クラスへ入るので、記号を通さない。"""
        with self.assertRaises(ValidationError):
            validate_blocks(
                [
                    {
                        "type": "code",
                        "data": {"language": 'py" onload="alert(1)', "code": "x"},
                    }
                ]
            )

    def test_image_requires_positive_integer_id(self):
        for media_id in ("15", -1, 0, None, {"pk": 1}):
            with self.subTest(media_id=media_id):
                with self.assertRaises(ValidationError):
                    validate_blocks(
                        [{"type": "image", "data": {"media_id": media_id}}]
                    )

    def test_table_rows_must_have_same_width(self):
        with self.assertRaises(ValidationError):
            validate_blocks(
                [
                    {
                        "type": "table",
                        "data": {"rows": [["a", "b"], ["c"]]},
                    }
                ]
            )

    def test_error_message_includes_block_position(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_blocks(
                [
                    {"type": "paragraph", "data": {"text": "ok"}},
                    {"type": "heading", "data": {"level": 9, "text": "ng"}},
                ]
            )
        self.assertIn("2 番目", str(ctx.exception))

    def test_disabled_plugin_block_is_rejected(self):
        register_plugin(
            PluginDefinition(
                "sample",
                "Sample",
                "1",
                "",
                blocks=(
                    PluginBlock(
                        "sample.link",
                        "Link",
                        lambda data: {"url": "/safe"},
                        "blog/blocks/cta.html",
                        (),
                    ),
                ),
            )
        )
        PluginActivation.objects.create(key="sample", enabled=False)

        with self.assertRaises(ValidationError):
            validate_blocks(
                [{"type": "sample.link", "data": {"url": "javascript:alert(1)"}}]
            )

    def test_enabled_plugin_block_uses_normalized_validator_result(self):
        register_plugin(
            PluginDefinition(
                "sample",
                "Sample",
                "1",
                "",
                blocks=(
                    PluginBlock(
                        "sample.link",
                        "Link",
                        lambda data: {"url": "/safe"},
                        "blog/blocks/cta.html",
                        (),
                    ),
                ),
            )
        )
        PluginActivation.objects.create(key="sample", enabled=True)

        article = create_article(title="plugin", category=create_category(name="plugin"))
        article.blocks = [
            {"type": "sample.link", "data": {"url": "javascript:alert(1)"}}
        ]
        article.save()
        article.refresh_from_db()

        self.assertEqual(article.blocks[0]["data"], {"url": "/safe"})


class BlocksToPlainTextTests(TestCase):
    def test_extracts_only_content(self):
        text = blocks_to_plain_text(
            [
                {"type": "heading", "data": {"level": 2, "text": "見出し"}},
                {"type": "paragraph", "data": {"text": "本文です"}},
                {"type": "image", "data": {"media_id": 1, "alt": "説明"}},
            ]
        )
        self.assertIn("見出し", text)
        self.assertIn("本文です", text)
        # キー名は含まれない（JSON をそのまま検索対象にしないため）
        self.assertNotIn("heading", text)
        self.assertNotIn("media_id", text)


class ArticleBlockIntegrationTests(TestCase):
    def setUp(self):
        self.category = create_category()

    def test_saving_blocks_mirrors_body(self):
        article = create_article(title="ブロック記事", category=self.category)
        article.blocks = [
            {"type": "heading", "data": {"level": 2, "text": "第1章"}},
            {"type": "paragraph", "data": {"text": "検索でヒットする本文"}},
        ]
        article.save()

        article.refresh_from_db()
        self.assertIn("検索でヒットする本文", article.body)
        self.assertTrue(article.uses_blocks)

    def test_search_finds_block_content(self):
        """ブロックで書いた記事も検索できる。"""
        article = create_article(title="検索対象", category=self.category)
        article.blocks = [
            {"type": "paragraph", "data": {"text": "ゼロトラストについて"}}
        ]
        article.save()

        found = Article.objects.published().search("ゼロトラスト")
        self.assertIn(article, found)

    def test_search_does_not_match_json_keys(self):
        article = create_article(title="キー名テスト", category=self.category)
        article.blocks = [{"type": "paragraph", "data": {"text": "普通の本文"}}]
        article.save()

        # "paragraph" は JSON のキーの値だが、本文には含まれない。
        self.assertNotIn(article, Article.objects.published().search("paragraph"))


class BlockRenderingTests(TestCase):
    def setUp(self):
        self.category = create_category()

    def _render(self, blocks):
        article = create_article(title="描画テスト", category=self.category)
        article.blocks = blocks
        article.save()
        return self.client.get(article.get_absolute_url()).content.decode()

    def test_heading_and_paragraph_are_rendered(self):
        html = self._render(
            [
                {"type": "heading", "data": {"level": 2, "text": "章タイトル"}},
                {"type": "paragraph", "data": {"text": "段落の中身"}},
            ]
        )
        self.assertIn("<h2 class=\"block-heading\">章タイトル</h2>", html)
        self.assertIn("段落の中身", html)

    def test_html_in_block_text_is_escaped(self):
        """ブロックの中身は必ずエスケープされる。"""
        html = self._render(
            [
                {
                    "type": "paragraph",
                    "data": {"text": "<script>alert('xss')</script>"},
                }
            ]
        )
        self.assertNotIn("<script>alert('xss')</script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_code_block_escapes_content(self):
        html = self._render(
            [
                {
                    "type": "code",
                    "data": {"language": "html", "code": "<div>&amp;</div>"},
                }
            ]
        )
        self.assertIn('class="language-html"', html)
        self.assertNotIn("<div>&amp;</div>", html)

    def test_reusable_block_is_expanded(self):
        reusable = ReusableBlock.objects.create(
            name="共通の注意書き",
            blocks=[{"type": "note", "data": {"variant": "info", "text": "共通文言"}}],
        )
        html = self._render([{"type": "reusable", "data": {"reusable_id": reusable.pk}}])
        self.assertIn("共通文言", html)

    def test_reusable_block_recursion_is_bounded(self):
        """再利用ブロックが互いを参照しても無限ループしない。"""
        first = ReusableBlock.objects.create(name="A", blocks=[])
        second = ReusableBlock.objects.create(
            name="B", blocks=[{"type": "reusable", "data": {"reusable_id": first.pk}}]
        )
        first.blocks = [{"type": "reusable", "data": {"reusable_id": second.pk}}]
        first.save()

        # 例外にならず、有限時間で返ること。
        html = self._render([{"type": "reusable", "data": {"reusable_id": first.pk}}])
        self.assertIn("<article", html)

    def test_related_block_excludes_unpublished(self):
        draft = create_article(
            title="関連の下書き",
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        published = create_article(title="関連の公開記事", category=self.category)

        html = self._render(
            [
                {
                    "type": "related",
                    "data": {"article_ids": [draft.pk, published.pk]},
                }
            ]
        )
        self.assertIn("関連の公開記事", html)
        self.assertNotIn("関連の下書き", html)

    def test_plugin_activation_lookup_is_constant_per_render(self):
        register_plugin(
            PluginDefinition(
                "sample",
                "Sample",
                "1",
                "",
                blocks=(
                    PluginBlock(
                        "sample.note",
                        "Note",
                        lambda data: {"variant": "info", "text": "safe"},
                        "blog/blocks/note.html",
                        (),
                    ),
                ),
            )
        )
        self.addCleanup(clear_registry)
        PluginActivation.objects.create(key="sample", enabled=True)
        article = create_article(title="query", category=self.category)
        article.blocks = [
            {"type": "sample.note", "data": {"text": "safe"}} for _ in range(300)
        ]
        article.save()

        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(article.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        activation_queries = [
            query
            for query in queries
            if "cms_plugins_pluginactivation" in query["sql"].lower()
        ]
        self.assertEqual(len(activation_queries), 1)
