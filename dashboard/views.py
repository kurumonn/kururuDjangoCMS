"""編集者向けダッシュボード。

Django の管理画面は「モデルを直接いじる道具」であって、
日々の編集作業のための画面ではない。
「いま自分が何をすべきか」が分かる画面を別に用意する。
"""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.utils import timezone
from django.views.generic import TemplateView

from blog.models import Article
from comments.models import Comment
from core.models import AuditLog


CMS_DASHBOARD_PERMISSIONS = (
    "blog.add_article",
    "blog.change_article",
    "blog.publish_article",
    "blog.review_article",
    "comments.change_comment",
    "core.view_auditlog",
)


class DashboardView(LoginRequiredMixin, TemplateView):
    """ダッシュボードのトップ。"""

    template_name = "dashboard/index.html"

    def dispatch(self, request, *args, **kwargs):
        # 未ログインは LoginRequiredMixin に任せてログイン画面へ送る。
        # ログイン済みで CMS 権限が無い場合だけ 403 にする。
        user = self.request.user
        if user.is_authenticated and not any(
            user.has_perm(perm) for perm in CMS_DASHBOARD_PERMISSIONS
        ):
            raise PermissionDenied("ダッシュボードを表示する権限がありません。")
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        now = timezone.now()
        can_review = user.has_perm("blog.review_article")

        # レビュー担当者だけが全記事を扱う。それ以外は所有記事に限定する。
        visible_articles = Article.objects.all()
        if not can_review:
            visible_articles = visible_articles.filter(author=user)

        # 集計を1本のクエリにまとめる。
        # status ごとに count() を4回呼ぶと、その分だけ往復が増える。
        counts = visible_articles.aggregate(
            published=Count(
                "pk",
                filter=Q(status=Article.Status.PUBLISHED, published_at__lte=now),
            ),
            scheduled=Count(
                "pk",
                filter=Q(status=Article.Status.PUBLISHED, published_at__gt=now),
            ),
            draft=Count("pk", filter=Q(status=Article.Status.DRAFT)),
            review=Count("pk", filter=Q(status=Article.Status.REVIEW)),
        )
        context["counts"] = counts
        can_moderate_comments = user.has_perm("comments.change_comment")
        context["pending_comment_count"] = (
            Comment.objects.pending().count() if can_moderate_comments else 0
        )

        context["can_review"] = can_review

        # レビュー待ち。承認権限が無い人には自分の依頼分だけ見せる。
        review_queue = Article.objects.filter(
            status=Article.Status.REVIEW
        ).with_related()
        if not can_review:
            review_queue = review_queue.filter(author=user)
        context["review_queue"] = review_queue[:10]

        # 予約投稿の一覧。「公開したつもりが未来日付だった」を見つけやすくする。
        context["scheduled_articles"] = (
            visible_articles.filter(
                status=Article.Status.PUBLISHED, published_at__gt=now
            )
            .with_related()
            .order_by("published_at")[:10]
        )

        context["my_drafts"] = (
            Article.objects.filter(author=user, status=Article.Status.DRAFT)
            .with_related()
            .order_by("-updated_at")[:10]
        )

        if can_moderate_comments:
            context["pending_comments"] = (
                Comment.objects.pending().select_related("article", "author")[:10]
            )

        if user.has_perm("core.view_auditlog"):
            context["recent_logs"] = AuditLog.objects.select_related("actor")[:15]

        return context
