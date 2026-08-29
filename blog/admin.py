from django.contrib import admin

from .models import Article, ArticleRevision, Category, ReusableBlock, Tag
from .permissions import can_edit, can_publish, can_review


@admin.register(ReusableBlock)
class ReusableBlockAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "updated_at")
    search_fields = ("name", "description")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "article_count")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="記事数")
    def article_count(self, obj: Category) -> int:
        return obj.articles.count()


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "category", "status", "published_at")
    list_filter = ("status", "category", "tags")
    search_fields = ("title", "body")
    # autocomplete_fields は参照先 Admin の search_fields を利用する。
    # CategoryAdmin / TagAdmin に search_fields が無いと admin.E040 で落ちる。
    autocomplete_fields = ("category", "tags")
    date_hierarchy = "published_at"
    # スラッグは save() で自動生成されるが、管理画面では手入力もできるようにする。
    prepopulated_fields = {"slug": ("title",)}

    fieldsets = (
        (None, {"fields": ("title", "slug", "body", "blocks")}),
        ("分類", {"fields": ("category", "tags")}),
        ("メディア", {"fields": ("featured_image",)}),
        ("公開", {"fields": ("status", "published_at", "author")}),
        (
            "SEO",
            {
                "classes": ("collapse",),
                "fields": (
                    "seo_title",
                    "seo_description",
                    "canonical_url",
                    "og_image",
                    "noindex",
                ),
            },
        ),
    )

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if can_review(request.user):
            return queryset
        return queryset.filter(author=request.user)

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not can_publish(request.user):
            readonly.extend(("status", "published_at"))
        return tuple(readonly)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None:
            return True
        # 公開中の本文は publish 権限なしで管理画面から直接変えない。
        if obj.status == Article.Status.PUBLISHED and not can_publish(request.user):
            return False
        return can_edit(request.user, obj)

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        return obj is None or can_edit(request.user, obj)

    def save_model(self, request, obj, form, change):
        # 著者が未設定なら、操作したユーザーを著者にする。
        if not obj.author_id:
            obj.author = request.user
        # 管理画面からの編集でも履歴を残す。CMS 画面だけで履歴を取ると、
        # 「管理画面から直したときだけ履歴が飛ぶ」という穴ができる。
        if change and obj.pk:
            before = Article.objects.filter(pk=obj.pk).first()
            if before is not None:
                before.snapshot(created_by=request.user, note="管理画面での編集前")
        super().save_model(request, obj, form, change)


@admin.register(ArticleRevision)
class ArticleRevisionAdmin(admin.ModelAdmin):
    """履歴は読むだけ。管理画面から書き換えられては履歴の意味が無い。"""

    list_display = ("article", "title", "status", "created_by", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("title", "body", "article__title")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if not request.user.has_perm("blog.change_article"):
            return queryset.none()
        if can_review(request.user):
            return queryset
        return queryset.filter(article__author=request.user)

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        if obj is None:
            return request.user.has_perm("blog.change_article")
        return can_edit(request.user, obj.article)
