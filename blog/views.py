"""ブログのビュー。

3日目で CRUD、5日目で編集ワークフロー（下書き→レビュー→公開）を作る。

  GET  /                              記事一覧
  GET  /search/                       サイト内検索
  GET  /articles/<slug>/              記事詳細
  GET/POST /articles/new/             投稿
  GET/POST /articles/<slug>/edit/     編集
  POST /articles/<slug>/delete/       削除
  POST /articles/<slug>/submit/       レビュー依頼
  POST /articles/<slug>/approve/      承認して公開
  POST /articles/<slug>/reject/       差し戻し
  GET  /articles/<slug>/revisions/    版の一覧
  POST /articles/<slug>/revisions/<pk>/restore/  復元
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    UpdateView,
    View,
)

from core.models import AuditLog, record

from .forms import ArticleForm
from .models import Article, ArticleRevision, Category, Tag
from .permissions import (
    can_edit as _can_edit,
    can_publish as _can_publish,
    can_review as _can_review,
)


# ---------------------------------------------------------------------------
# 閲覧
# ---------------------------------------------------------------------------
class ArticleListView(ListView):
    """公開済み記事の一覧。"""

    model = Article
    template_name = "blog/article_list.html"
    context_object_name = "articles"
    paginate_by = 10

    def get_queryset(self):
        # published() を通さないと、下書きや予約投稿が一般利用者へ漏れる。
        return Article.objects.published().with_related()


class ArticleDetailView(DetailView):
    """記事詳細。

    未公開記事は、著者本人とスタッフだけが確認できる（プレビュー）。
    それ以外には 404 を返す。403 を返すと「その slug の記事は存在する」
    という情報が漏れるため、存在自体を隠す。
    """

    model = Article
    template_name = "blog/article_detail.html"
    context_object_name = "article"

    def get_queryset(self):
        return Article.objects.all().with_related()

    def get_object(self, queryset=None):
        article = super().get_object(queryset)
        if article.is_visible_to_public:
            return article

        user = self.request.user
        if _can_edit(user, article) or _can_review(user):
            return article

        raise Http404("記事が見つかりません。")

    def get_context_data(self, **kwargs):
        from comments.forms import CommentForm

        context = super().get_context_data(**kwargs)
        article = context["article"]
        user = self.request.user

        context["is_preview"] = not article.is_visible_to_public
        context["can_edit"] = _can_edit(user, article)
        context["can_review"] = _can_review(user)
        context["can_submit_review"] = (
            _can_edit(user, article) and article.status == Article.Status.DRAFT
        )
        # 一般利用者へ見せるのは承認済みコメントだけ。
        context["comments"] = article.comments.approved()
        context["comment_form"] = CommentForm(user=user)
        context["related_articles"] = article.related_articles()
        # パンくずリスト。[(表示名, パス), ...] の順で、最後が現在地。
        context["crumbs"] = [
            ("ホーム", reverse("blog:article_list")),
            (article.category.name, article.category.get_absolute_url()),
            (article.title, article.get_absolute_url()),
        ]
        return context


class SearchView(ArticleListView):
    """サイト内検索。

    検索語は URL のクエリ文字列（?q=...）で受け取る。
    検索は状態を変えない操作なので GET でよい。
    """

    template_name = "blog/search.html"

    def get_queryset(self):
        self.query = self.request.GET.get("q", "").strip()[:100]
        if not self.query:
            return Article.objects.none()
        return Article.objects.published().search(self.query).with_related()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.query
        return context


class CategoryArticleListView(ArticleListView):
    """カテゴリ別の記事一覧。"""

    def get_queryset(self):
        self.category = get_object_or_404(Category, slug=self.kwargs["slug"])
        return super().get_queryset().filter(category=self.category)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["heading"] = f"カテゴリ: {self.category.name}"
        return context


class TagArticleListView(ArticleListView):
    """タグ別の記事一覧。"""

    def get_queryset(self):
        self.tag = get_object_or_404(Tag, slug=self.kwargs["slug"])
        return super().get_queryset().filter(tags=self.tag)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["heading"] = f"タグ: {self.tag.name}"
        return context


# ---------------------------------------------------------------------------
# 編集
# ---------------------------------------------------------------------------
class ArticleOwnerMixin:
    """自分の記事か、スタッフ権限があるときだけ通す。"""

    def get_object(self, queryset=None):
        article = super().get_object(queryset)
        if not _can_edit(self.request.user, article):
            raise PermissionDenied("この記事を編集する権限がありません。")
        return article


class ArticleFormUserMixin:
    """フォームへログイン中のユーザーを渡す（公開権限の判定に使う）。"""

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class ArticleCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ArticleFormUserMixin, CreateView
):
    model = Article
    form_class = ArticleForm
    template_name = "blog/article_form.html"
    permission_required = "blog.add_article"

    def form_valid(self, form):
        # 著者はフォームの値ではなく、ログイン中のユーザーで決める。
        form.instance.author = self.request.user
        response = super().form_valid(form)
        record(
            AuditLog.Action.CREATE,
            actor=self.request.user,
            target=self.object,
            request=self.request,
            status=self.object.status,
        )
        messages.success(self.request, "記事を作成しました。")
        return response

    def get_context_data(self, **kwargs):
        from .blocks import block_editor_catalog

        context = super().get_context_data(**kwargs)
        context["form_title"] = "記事を作成"
        context["block_editor_catalog"] = block_editor_catalog()
        return context


class ArticleUpdateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ArticleOwnerMixin,
    ArticleFormUserMixin,
    UpdateView,
):
    model = Article
    form_class = ArticleForm
    template_name = "blog/article_form.html"
    permission_required = "blog.change_article"

    @transaction.atomic
    def form_valid(self, form):
        # 更新の「前」に現在の内容を版として保存する。
        # 保存後に呼ぶと、変更前の内容がどこにも残らない。
        before = Article.objects.get(pk=self.object.pk)
        before.snapshot(created_by=self.request.user, note="編集前の自動保存")

        response = super().form_valid(form)

        record(
            AuditLog.Action.UPDATE,
            actor=self.request.user,
            target=self.object,
            request=self.request,
            from_status=before.status,
            to_status=self.object.status,
        )
        messages.success(self.request, "記事を更新しました。")
        return response

    def get_context_data(self, **kwargs):
        from .blocks import block_editor_catalog

        context = super().get_context_data(**kwargs)
        context["form_title"] = "記事を編集"
        context["revision_count"] = self.object.revisions.count()
        context["block_editor_catalog"] = block_editor_catalog()
        return context


class ArticleDeleteView(
    LoginRequiredMixin, PermissionRequiredMixin, ArticleOwnerMixin, DeleteView
):
    model = Article
    template_name = "blog/article_confirm_delete.html"
    permission_required = "blog.delete_article"
    success_url = reverse_lazy("blog:article_list")

    def form_valid(self, form):
        # 削除の記録は削除の前に取る。
        # 後に取ると、対象が消えていて何を消したのか書けない。
        record(
            AuditLog.Action.DELETE,
            actor=self.request.user,
            target=self.object,
            request=self.request,
            title=self.object.title,
        )
        messages.success(self.request, "記事を削除しました。")
        return super().form_valid(form)


# ---------------------------------------------------------------------------
# 編集ワークフロー
# ---------------------------------------------------------------------------
class ArticleWorkflowView(LoginRequiredMixin, View):
    """状態を変える操作の共通土台。

    すべて POST のみ。GET で状態が変わる URL を作ってはいけない。
    """

    def get_article(self) -> Article:
        return get_object_or_404(Article, slug=self.kwargs["slug"])

    def redirect_to_article(self, article: Article) -> HttpResponseRedirect:
        return HttpResponseRedirect(article.get_absolute_url())


class ArticleSubmitReviewView(ArticleWorkflowView):
    """下書きをレビュー待ちにする（投稿者の操作）。"""

    def post(self, request, slug):
        article = self.get_article()
        if not _can_edit(request.user, article):
            raise PermissionDenied("この記事を操作する権限がありません。")
        if article.status != Article.Status.DRAFT:
            messages.error(request, "下書きの記事だけがレビュー依頼できます。")
            return self.redirect_to_article(article)

        article.status = Article.Status.REVIEW
        article.save(update_fields=["status", "updated_at"])
        record(
            AuditLog.Action.SUBMIT_REVIEW,
            actor=request.user,
            target=article,
            request=request,
        )
        messages.success(request, "レビューを依頼しました。")
        return self.redirect_to_article(article)


class ArticleApproveView(ArticleWorkflowView):
    """レビュー待ちの記事を承認して公開する（編集者の操作）。"""

    @transaction.atomic
    def post(self, request, slug):
        article = get_object_or_404(
            Article.objects.select_for_update(), slug=self.kwargs["slug"]
        )
        if not (
            _can_edit(request.user, article)
            and _can_review(request.user)
            and _can_publish(request.user)
        ):
            raise PermissionDenied("記事を承認・公開する権限がありません。")
        if article.status != Article.Status.REVIEW:
            messages.error(request, "レビュー待ちの記事だけが承認できます。")
            return self.redirect_to_article(article)

        # 自分の記事を自分で承認できてしまうと、承認フローの意味が無くなる。
        if article.author_id == request.user.pk and not request.user.is_superuser:
            messages.error(request, "自分の記事は自分で承認できません。")
            return self.redirect_to_article(article)

        try:
            reviewed_version = int(request.POST.get("version", ""))
        except (TypeError, ValueError):
            messages.error(request, "確認した記事の版を特定できないため承認を中止しました。")
            return self.redirect_to_article(article)
        if reviewed_version != article.version:
            messages.error(
                request,
                "確認後に記事が更新されたため承認を中止しました。"
                "本文をもう一度確認してください。",
            )
            return self.redirect_to_article(article)

        article.status = Article.Status.PUBLISHED
        if not article.published_at:
            article.published_at = timezone.now()
        article.save(update_fields=["status", "published_at", "updated_at"])

        record(
            AuditLog.Action.APPROVE,
            actor=request.user,
            target=article,
            request=request,
            published_at=article.published_at.isoformat(),
        )
        if article.is_scheduled:
            messages.success(
                request,
                f"承認しました。{article.published_at:%Y年%m月%d日 %H:%M} に公開されます。",
            )
        else:
            messages.success(request, "承認して公開しました。")
        return self.redirect_to_article(article)


class ArticleRejectView(ArticleWorkflowView):
    """レビュー待ちの記事を下書きへ差し戻す（編集者の操作）。"""

    def post(self, request, slug):
        article = self.get_article()
        if not (_can_edit(request.user, article) and _can_review(request.user)):
            raise PermissionDenied("記事を差し戻す権限がありません。")
        if article.status != Article.Status.REVIEW:
            messages.error(request, "レビュー待ちの記事だけが差し戻せます。")
            return self.redirect_to_article(article)

        note = request.POST.get("note", "")[:200]
        article.status = Article.Status.DRAFT
        article.save(update_fields=["status", "updated_at"])
        record(
            AuditLog.Action.REJECT,
            actor=request.user,
            target=article,
            request=request,
            note=note,
        )
        messages.success(request, "下書きへ差し戻しました。")
        return self.redirect_to_article(article)


class ArticleRevisionListView(LoginRequiredMixin, ListView):
    """記事の版の一覧。"""

    template_name = "blog/revision_list.html"
    context_object_name = "revisions"
    paginate_by = 20

    def get_queryset(self):
        self.article = get_object_or_404(Article, slug=self.kwargs["slug"])
        if not _can_edit(self.request.user, self.article):
            raise PermissionDenied("この記事の履歴を見る権限がありません。")
        return self.article.revisions.select_related("created_by")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["article"] = self.article
        return context


class ArticleRevisionRestoreView(ArticleWorkflowView):
    """指定した版の内容を記事へ書き戻す。"""

    def post(self, request, slug, pk):
        article = self.get_article()
        if not _can_edit(request.user, article):
            raise PermissionDenied("この記事を編集する権限がありません。")

        with transaction.atomic():
            article = get_object_or_404(
                Article.objects.select_for_update(), pk=article.pk
            )
            if article.status == Article.Status.PUBLISHED:
                messages.error(
                    request,
                    "公開中の記事へ直接復元はできません。"
                    "先に編集画面で下書きまたはレビュー待ちへ戻してください。",
                )
                return self.redirect_to_article(article)
            revision = get_object_or_404(ArticleRevision, pk=pk, article=article)
            revision.restore_to_article(restored_by=request.user)
            record(
                AuditLog.Action.RESTORE,
                actor=request.user,
                target=article,
                request=request,
                revision_id=revision.pk,
                revision_created_at=revision.created_at.isoformat(),
            )

        messages.success(
            request,
            f"{revision.created_at:%Y年%m月%d日 %H:%M} の内容へ復元しました。",
        )
        return self.redirect_to_article(article)
