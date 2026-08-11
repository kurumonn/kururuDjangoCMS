"""ブラウザーのブログ編集導線を、Django の実フォームで通すテスト。"""

import json

from django.test import TestCase
from django.urls import reverse

from .factories import create_article, create_author, create_category

PASSWORD = "test-pass-phrase-1234"


class ArticleEditorIntegrationTests(TestCase):
    def setUp(self):
        self.category = create_category(name="編集テスト")
        self.author = create_author(username="editor-flow")
        self.article = create_article(
            title="編集フロー",
            author=self.author,
            category=self.category,
            status="draft",
            published_at=None,
            body="旧本文",
            blocks=[],
        )
        self.url = reverse("blog:article_update", args=[self.article.slug])
        self.client.login(username="editor-flow", password=PASSWORD)

    def test_edit_form_contains_csrf_and_block_editor_contract(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="csrfmiddlewaretoken"')
        self.assertContains(response, 'id="block-editor"')
        self.assertContains(
            response, reverse("dashboard:autosave", args=[self.article.pk])
        )
        self.assertContains(response, 'src="/static/js/block-editor.js"')

    def test_submit_persists_blocks_and_creates_revision(self):
        blocks = [
            {"type": "heading", "data": {"level": 2, "text": "追記"}},
            {"type": "paragraph", "data": {"text": "保存された本文"}},
        ]
        response = self.client.post(
            self.url,
            {
                "title": "編集フロー（更新）",
                "body": "",
                "blocks": json.dumps(blocks, ensure_ascii=False),
                "category": self.category.pk,
                "status": "draft",
                "published_at": "",
                "seo_title": "",
                "seo_description": "",
                "canonical_url": "",
                "noindex": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "編集フロー（更新）")
        self.assertEqual(self.article.blocks, blocks)
        self.assertIn("保存された本文", self.article.body)
        self.assertEqual(self.article.revisions.count(), 1)

    def test_expired_session_gets_json_from_autosave(self):
        self.client.logout()
        response = self.client.post(
            reverse("dashboard:autosave", args=[self.article.pk]),
            data=json.dumps(
                {"title": "失敗", "blocks": [], "version": self.article.version}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response["Content-Type"].split(";")[0], "application/json")
        self.assertFalse(response.json()["ok"])
