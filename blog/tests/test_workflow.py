"""5日目: 下書き・レビュー・承認・予約投稿・リビジョンのテスト。"""

import json
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from blog.models import Article, ArticleRevision
from core.models import AuditLog

from .factories import (
    add_totp,
    create_article,
    create_author,
    create_category,
    create_editor,
    create_staff,
    create_user,
    grant,
    login_staff,
    verify_email,
)

PASSWORD = "test-pass-phrase-1234"


class PublishPermissionTests(TestCase):
    """公開は独立した権限。記事を書けることと公開できることを分ける。"""

    def setUp(self):
        self.category = create_category()
        self.url = reverse("blog:article_create")

    def _payload(self, status):
        return {
            "title": "権限テスト記事",
            "body": "本文",
            "category": self.category.pk,
            "status": status,
            "published_at": "",
        }

    def test_author_cannot_choose_published_in_form(self):
        author = create_author(username="plain-author")
        login_staff(self.client, author)

        response = self.client.get(self.url)
        choices = dict(response.context["form"].fields["status"].choices)
        self.assertNotIn(Article.Status.PUBLISHED, choices)
        self.assertIn(Article.Status.DRAFT, choices)
        self.assertIn(Article.Status.REVIEW, choices)

    def test_author_posting_published_is_rejected(self):
        """画面に出ていなくても POST は直接送れる。サーバー側で必ず弾く。"""
        author = create_author(username="sneaky")
        login_staff(self.client, author)

        response = self.client.post(self.url, self._payload(Article.Status.PUBLISHED))
        self.assertEqual(response.status_code, 200)  # フォーム再表示
        self.assertFalse(Article.objects.filter(title="権限テスト記事").exists())

    def test_editor_can_publish_directly(self):
        editor = create_editor(username="direct-editor")
        login_staff(self.client, editor)

        response = self.client.post(self.url, self._payload(Article.Status.PUBLISHED))
        self.assertEqual(response.status_code, 302)

        article = Article.objects.get(title="権限テスト記事")
        self.assertTrue(article.is_visible_to_public)


class AdminArticlePermissionTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="admin-article-author")
        self.staff = create_staff(username="admin-article-staff")
        self.article = create_article(
            title="他人の公開記事",
            author=self.author,
            category=self.category,
            status=Article.Status.PUBLISHED,
        )

    def test_unrelated_staff_cannot_open_article_change_form(self):
        login_staff(self.client, self.staff)

        response = self.client.get(
            reverse("admin:blog_article_change", args=[self.article.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:index"), response.url)

    def test_non_publisher_cannot_choose_publication_fields_on_add(self):
        login_staff(self.client, self.staff)

        response = self.client.get(reverse("admin:blog_article_add"))

        self.assertEqual(response.status_code, 200)
        fields = response.context["adminform"].form.fields
        self.assertNotIn("status", fields)
        self.assertNotIn("published_at", fields)


class ReviewWorkflowTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="wf-author")
        self.editor = create_editor(username="wf-editor")
        self.article = create_article(
            title="ワークフロー記事",
            author=self.author,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        self.submit_url = reverse("blog:article_submit_review", args=[self.article.slug])
        self.approve_url = reverse("blog:article_approve", args=[self.article.slug])
        self.reject_url = reverse("blog:article_reject", args=[self.article.slug])

    def _refresh(self):
        self.article.refresh_from_db()
        return self.article

    def test_workflow_urls_reject_get(self):
        """状態を変える URL は GET を受け付けない。"""
        login_staff(self.client, self.author)
        self.assertEqual(self.client.get(self.submit_url).status_code, 405)

    def test_author_submits_for_review(self):
        login_staff(self.client, self.author)
        response = self.client.post(self.submit_url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)

    def test_stranger_cannot_submit(self):
        stranger = create_author(username="wf-stranger")
        login_staff(self.client, stranger)
        response = self.client.post(self.submit_url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._refresh().status, Article.Status.DRAFT)

    def test_author_without_review_permission_cannot_approve(self):
        self.article.status = Article.Status.REVIEW
        self.article.save()

        login_staff(self.client, self.author)
        response = self.client.post(self.approve_url)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)

    def test_reviewer_without_change_permission_cannot_approve_or_reject(self):
        reviewer = grant(
            create_user(username="wf-review-only"),
            "blog.review_article",
            "blog.publish_article",
        )
        add_totp(reviewer)
        verify_email(reviewer)
        self.article.status = Article.Status.REVIEW
        self.article.save()
        login_staff(self.client, reviewer)

        self.assertEqual(self.client.post(self.approve_url).status_code, 403)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)
        self.assertEqual(self.client.post(self.reject_url).status_code, 403)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)

    def test_editor_approves_and_publishes(self):
        self.article.status = Article.Status.REVIEW
        self.article.save()

        login_staff(self.client, self.editor)
        response = self.client.post(
            self.approve_url, {"version": self.article.version}
        )
        self.assertEqual(response.status_code, 302)

        article = self._refresh()
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertIsNotNone(article.published_at)
        self.assertTrue(article.is_visible_to_public)

    def test_approval_without_reviewed_version_is_rejected(self):
        self.article.status = Article.Status.REVIEW
        self.article.save()
        login_staff(self.client, self.editor)

        response = self.client.post(self.approve_url)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)

    def test_autosave_after_review_requires_reinspection_before_approval(self):
        self.article.status = Article.Status.REVIEW
        self.article.save()
        reviewed_version = self.article.version
        login_staff(self.client, self.author)
        response = self.client.post(
            reverse("dashboard:autosave", args=[self.article.pk]),
            data=json.dumps(
                {
                    "title": "確認後に差し替えた題名",
                    "blocks": [
                        {
                            "type": "paragraph",
                            "data": {"text": "確認後に差し替えた本文"},
                        }
                    ],
                    "version": reviewed_version,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        login_staff(self.client, self.editor)
        response = self.client.post(
            self.approve_url, {"version": reviewed_version}
        )

        self.assertEqual(response.status_code, 302)
        article = self._refresh()
        self.assertEqual(article.status, Article.Status.REVIEW)
        self.assertEqual(article.title, "確認後に差し替えた題名")

    def test_restore_after_review_requires_reinspection_before_approval(self):
        revision = self.article.snapshot(created_by=self.author, note="確認前")
        self.article.body = "レビュー担当が確認した本文"
        self.article.save()
        self.article.status = Article.Status.REVIEW
        self.article.save()
        reviewed_version = self.article.version
        login_staff(self.client, self.author)
        response = self.client.post(
            reverse(
                "blog:article_revision_restore",
                args=[self.article.slug, revision.pk],
            )
        )
        self.assertEqual(response.status_code, 302)

        login_staff(self.client, self.editor)
        response = self.client.post(
            self.approve_url, {"version": reviewed_version}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._refresh().status, Article.Status.REVIEW)

    def test_editor_cannot_approve_own_article(self):
        """自分の記事を自分で承認できると、承認フローが形だけになる。"""
        own = create_article(
            title="編集者自身の記事",
            author=self.editor,
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=None,
        )
        login_staff(self.client, self.editor)
        response = self.client.post(
            reverse("blog:article_approve", args=[own.slug])
        )
        self.assertEqual(response.status_code, 302)

        own.refresh_from_db()
        self.assertEqual(own.status, Article.Status.REVIEW)

    def test_cannot_approve_draft(self):
        login_staff(self.client, self.editor)
        self.client.post(self.approve_url)
        self.assertEqual(self._refresh().status, Article.Status.DRAFT)

    def test_editor_rejects_back_to_draft(self):
        self.article.status = Article.Status.REVIEW
        self.article.save()

        login_staff(self.client, self.editor)
        response = self.client.post(self.reject_url, {"note": "出典を追記してください"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self._refresh().status, Article.Status.DRAFT)

        entry = AuditLog.objects.filter(action=AuditLog.Action.REJECT).first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.detail["note"], "出典を追記してください")


class EditorCanEditOthersArticleTests(TestCase):
    """編集者は、管理画面権限（is_staff）が無くても他人の記事を編集できる。

    ここを is_staff だけで判定していると、
    「レビューして公開する役目なのに本文を直せない」状態になる。
    ブラウザーで編集画面を開いて 403 になり、初めて気づいた。
    """

    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="edit-author")
        self.editor = create_editor(username="edit-editor")
        self.article = create_article(
            title="他人の記事",
            author=self.author,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )

    def test_editor_is_not_staff(self):
        """前提の確認。is_staff を付けずに編集できることが要点。"""
        self.assertFalse(self.editor.is_staff)

    def test_editor_can_open_edit_form(self):
        login_staff(self.client, self.editor)
        response = self.client.get(
            reverse("blog:article_update", args=[self.article.slug])
        )
        self.assertEqual(response.status_code, 200)

    def test_editor_can_view_revisions(self):
        login_staff(self.client, self.editor)
        response = self.client.get(
            reverse("blog:article_revisions", args=[self.article.slug])
        )
        self.assertEqual(response.status_code, 200)

    def test_editor_can_autosave_others_article(self):
        """画面と自動保存 API で判定がそろっていること。"""
        import json

        from django.core.cache import cache

        cache.clear()
        login_staff(self.client, self.editor)
        response = self.client.post(
            reverse("dashboard:autosave", args=[self.article.pk]),
            data=json.dumps(
                {
                    "title": "編集者が直した題名",
                    "blocks": [{"type": "paragraph", "data": {"text": "本文"}}],
                    "version": self.article.version,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

    def test_plain_author_still_cannot_edit_others(self):
        """権限を広げすぎていないこと。投稿者は他人の記事を触れない。"""
        author = create_author(username="edit-plain")
        login_staff(self.client, author)
        response = self.client.get(
            reverse("blog:article_update", args=[self.article.slug])
        )
        self.assertEqual(response.status_code, 403)


class ScheduledPublishTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.editor = create_editor(username="sched-editor")

    def test_future_dated_article_is_hidden_until_time(self):
        article = create_article(
            title="予約公開の記事",
            category=self.category,
            status=Article.Status.PUBLISHED,
            published_at=timezone.now() + timedelta(hours=2),
        )
        self.assertTrue(article.is_scheduled)
        self.assertFalse(article.is_visible_to_public)

        response = self.client.get(reverse("blog:article_list"))
        self.assertNotContains(response, "予約公開の記事")

        # 公開時刻を過ぎれば、何もしなくても一覧へ出る（cron 不要）。
        article.published_at = timezone.now() - timedelta(minutes=1)
        article.save()
        response = self.client.get(reverse("blog:article_list"))
        self.assertContains(response, "予約公開の記事")

    def test_approving_keeps_future_publish_date(self):
        """予約日時が入っている記事を承認しても、日時は現在時刻へ潰さない。"""
        future = timezone.now() + timedelta(days=3)
        article = create_article(
            title="予約承認",
            author=create_author(username="sched-author"),
            category=self.category,
            status=Article.Status.REVIEW,
            published_at=future,
        )
        login_staff(self.client, self.editor)
        self.client.post(
            reverse("blog:article_approve", args=[article.slug]),
            {"version": article.version},
        )

        article.refresh_from_db()
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertEqual(article.published_at, future)
        self.assertTrue(article.is_scheduled)


class RevisionTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="rev-author")
        self.article = create_article(
            title="最初のタイトル",
            author=self.author,
            category=self.category,
            body="最初の本文",
            status=Article.Status.DRAFT,
            published_at=None,
        )
        self.update_url = reverse("blog:article_update", args=[self.article.slug])

    def _edit(self, title, body):
        return self.client.post(
            self.update_url,
            {
                "title": title,
                "body": body,
                "category": self.category.pk,
                "status": Article.Status.DRAFT,
                "published_at": "",
            },
        )

    def test_editing_creates_revision_of_previous_content(self):
        login_staff(self.client, self.author)
        self._edit("2番目のタイトル", "2番目の本文")

        revision = ArticleRevision.objects.get(article=self.article)
        # 保存されるのは「変更前」の内容。
        self.assertEqual(revision.title, "最初のタイトル")
        self.assertEqual(revision.body, "最初の本文")

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "2番目のタイトル")

    def test_restore_brings_back_old_content(self):
        login_staff(self.client, self.author)
        self._edit("2番目のタイトル", "2番目の本文")

        revision = ArticleRevision.objects.get(article=self.article)
        restore_url = reverse(
            "blog:article_revision_restore", args=[self.article.slug, revision.pk]
        )
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 302)

        self.article.refresh_from_db()
        self.assertEqual(self.article.title, "最初のタイトル")
        self.assertEqual(self.article.body, "最初の本文")

    def test_restore_also_snapshots_current_content(self):
        """復元は取り消せる。復元前の内容も版として残る。"""
        login_staff(self.client, self.author)
        self._edit("2番目のタイトル", "2番目の本文")

        revision = ArticleRevision.objects.get(article=self.article)
        self.client.post(
            reverse(
                "blog:article_revision_restore", args=[self.article.slug, revision.pk]
            )
        )

        titles = set(
            ArticleRevision.objects.filter(article=self.article).values_list(
                "title", flat=True
            )
        )
        self.assertIn("最初のタイトル", titles)
        self.assertIn("2番目のタイトル", titles)

    def test_model_refuses_to_restore_over_published_article(self):
        """公開中の承認済み本文を、モデルの直接呼び出しでも上書きさせない。"""
        editor = create_editor(username="rev-editor")
        article = create_article(
            title="公開中の記事",
            author=editor,
            category=self.category,
            body="公開時の本文",
            status=Article.Status.PUBLISHED,
        )
        revision = article.snapshot(created_by=editor, note="手動")

        article.body = "書き換え後"
        article.save()

        with self.assertRaises(ValidationError):
            revision.restore_to_article(restored_by=editor)

        article.refresh_from_db()
        self.assertEqual(article.body, "書き換え後")
        self.assertEqual(article.status, Article.Status.PUBLISHED)
        self.assertTrue(article.is_visible_to_public)

    def test_model_refetches_status_before_restoring(self):
        """呼出し側のArticleが古くても、DB上の公開状態を再検査する。"""
        revision = self.article.snapshot(created_by=self.author, note="下書き時")
        self.assertEqual(revision.article.status, Article.Status.DRAFT)
        Article.objects.filter(pk=self.article.pk).update(
            status=Article.Status.PUBLISHED,
            published_at=timezone.now(),
        )

        with self.assertRaises(ValidationError):
            revision.restore_to_article(restored_by=self.author)

        self.article.refresh_from_db()
        self.assertEqual(self.article.status, Article.Status.PUBLISHED)

    def test_restore_view_does_not_mutate_published_article(self):
        article = create_article(
            title="公開記事",
            author=self.author,
            category=self.category,
            body="承認済み本文",
            status=Article.Status.PUBLISHED,
        )
        revision = article.snapshot(created_by=self.author, note="公開時")
        article.body = "現在の公開本文"
        article.save()
        login_staff(self.client, self.author)

        response = self.client.post(
            reverse(
                "blog:article_revision_restore", args=[article.slug, revision.pk]
            )
        )

        self.assertEqual(response.status_code, 302)
        article.refresh_from_db()
        self.assertEqual(article.body, "現在の公開本文")
        self.assertEqual(article.status, Article.Status.PUBLISHED)

    def test_revoked_owner_cannot_restore_revision(self):
        from .factories import create_user

        owner = create_user(username="rev-revoked")
        article = create_article(
            title="権限剥奪記事",
            author=owner,
            category=self.category,
            status=Article.Status.DRAFT,
            published_at=None,
        )
        revision = article.snapshot(created_by=owner, note="保存版")
        self.client.login(username=owner.username, password=PASSWORD)

        response = self.client.post(
            reverse(
                "blog:article_revision_restore", args=[article.slug, revision.pk]
            )
        )

        self.assertEqual(response.status_code, 403)

    def test_stranger_cannot_view_revisions(self):
        stranger = create_author(username="rev-stranger")
        login_staff(self.client, stranger)
        response = self.client.get(
            reverse("blog:article_revisions", args=[self.article.slug])
        )
        self.assertEqual(response.status_code, 403)


class AuditLogTests(TestCase):
    def setUp(self):
        self.category = create_category()
        self.author = create_author(username="log-author")

    def test_create_is_recorded(self):
        login_staff(self.client, self.author)
        self.client.post(
            reverse("blog:article_create"),
            {
                "title": "ログ対象",
                "body": "本文",
                "category": self.category.pk,
                "status": Article.Status.DRAFT,
                "published_at": "",
            },
        )
        entry = AuditLog.objects.get(action=AuditLog.Action.CREATE)
        self.assertEqual(entry.actor, self.author)
        self.assertEqual(entry.target_repr, "ログ対象")
        self.assertEqual(entry.target_model, "article")

    def test_delete_is_recorded_with_title(self):
        article = create_article(
            title="消える記事", author=self.author, category=self.category
        )
        login_staff(self.client, self.author)
        self.client.post(reverse("blog:article_delete", args=[article.slug]))

        entry = AuditLog.objects.get(action=AuditLog.Action.DELETE)
        # 記事が消えても、何を消したかは残る。
        self.assertEqual(entry.detail["title"], "消える記事")
        self.assertFalse(Article.objects.filter(pk=article.pk).exists())

    def test_ip_is_hashed_in_log(self):
        login_staff(self.client, self.author)
        self.client.post(
            reverse("blog:article_create"),
            {
                "title": "IPログ",
                "body": "本文",
                "category": self.category.pk,
                "status": Article.Status.DRAFT,
                "published_at": "",
            },
            REMOTE_ADDR="203.0.113.42",
        )
        entry = AuditLog.objects.get(action=AuditLog.Action.CREATE)
        self.assertNotIn("203.0.113.42", entry.ip_hash)
        self.assertEqual(len(entry.ip_hash), 64)


class GroupSetupTests(TestCase):
    """setup_groups コマンドが役割を正しく作るか。"""

    def test_groups_are_created_with_expected_permissions(self):
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("setup_groups", verbosity=0)

        author_group = Group.objects.get(name="投稿者")
        editor_group = Group.objects.get(name="編集者")

        author_perms = set(
            author_group.permissions.values_list("codename", flat=True)
        )
        editor_perms = set(
            editor_group.permissions.values_list("codename", flat=True)
        )

        # 投稿者は記事を書けるが公開できない。
        self.assertIn("add_article", author_perms)
        self.assertNotIn("publish_article", author_perms)
        self.assertNotIn("review_article", author_perms)

        # 編集者は公開・承認ができる。
        self.assertIn("publish_article", editor_perms)
        self.assertIn("review_article", editor_perms)

    def test_command_is_idempotent(self):
        from django.contrib.auth.models import Group
        from django.core.management import call_command

        call_command("setup_groups", verbosity=0)
        first = Group.objects.get(name="編集者").permissions.count()
        call_command("setup_groups", verbosity=0)
        second = Group.objects.get(name="編集者").permissions.count()

        self.assertEqual(first, second)
        self.assertEqual(Group.objects.filter(name="編集者").count(), 1)
