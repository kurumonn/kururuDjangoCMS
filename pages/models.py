"""固定ページ。

記事（時系列で流れていくもの）と、固定ページ（会社概要・利用規約など
ずっと同じ場所にあるもの）は性質が違うため、モデルを分ける。
同じ Article に「これは固定ページ」フラグを足す設計にすると、
一覧・RSS・サイトマップのすべてで除外条件を書く羽目になる。
"""

from django.db import models
from django.urls import reverse
from django.utils import timezone

from blog.utils import unique_slugify
from seo.validators import validate_canonical_url


class PageQuerySet(models.QuerySet):
    def published(self):
        return self.filter(
            status=Page.Status.PUBLISHED,
            published_at__isnull=False,
            published_at__lte=timezone.now(),
        )


class Page(models.Model):
    """固定ページ。"""

    class Status(models.TextChoices):
        DRAFT = "draft", "下書き"
        PUBLISHED = "published", "公開"

    title = models.CharField("タイトル", max_length=200)
    slug = models.SlugField("スラッグ", max_length=220, unique=True, blank=True)
    body = models.TextField("本文")

    seo_title = models.CharField(
        "SEOタイトル",
        max_length=70,
        blank=True,
        default="",
        help_text="空なら固定ページのタイトルを使います。",
    )
    seo_description = models.CharField(
        "SEO説明文",
        max_length=160,
        blank=True,
        default="",
        help_text="空なら本文の冒頭を使います。",
    )
    canonical_url = models.URLField(
        "正規URL",
        blank=True,
        default="",
        help_text="別URLを正規とする場合だけ指定します。",
        validators=[validate_canonical_url],
    )
    noindex = models.BooleanField("検索エンジンから除外", default=False)
    og_image = models.ForeignKey(
        "media_library.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="og_pages",
        verbose_name="OG画像",
    )

    status = models.CharField(
        "公開状態", max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    published_at = models.DateTimeField("公開日時", null=True, blank=True)

    show_in_footer = models.BooleanField("フッターに表示", default=False)
    menu_order = models.PositiveIntegerField("表示順", default=0)

    created_at = models.DateTimeField("作成日時", auto_now_add=True)
    updated_at = models.DateTimeField("更新日時", auto_now=True)

    objects = PageQuerySet.as_manager()

    class Meta:
        verbose_name = "固定ページ"
        verbose_name_plural = "固定ページ"
        ordering = ["menu_order", "title"]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slugify(Page, self.title, instance=self)
        super().save(*args, **kwargs)

    def get_absolute_url(self) -> str:
        return reverse("pages:detail", kwargs={"slug": self.slug})

    @property
    def display_seo_title(self) -> str:
        return self.seo_title or self.title

    @property
    def display_seo_description(self) -> str:
        if self.seo_description:
            return self.seo_description
        flattened = " ".join(self.body.split())
        return flattened[:157] + "…" if len(flattened) > 160 else flattened

    @property
    def display_og_image(self):
        return self.og_image
