"""サイト全体の設定とテーマ。

WordPress でいう「サイト設定」と「テーマ」に相当する部分を、
コードではなくデータベースで持つ。デプロイし直さずに
サイト名・色・サイドバーの並びを変えられるようにするため。
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from .themes import DEFAULT_THEME_KEY, validate_theme_key

# CSS へ差し込む色は自由入力にしない。
# 任意の文字列を CSS に埋め込めると、そこから CSS インジェクションが成立する。
HEX_COLOR = RegexValidator(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$",
    "色は #rgb または #rrggbb の形式で指定してください。",
)


class SiteSetting(models.Model):
    """サイト全体の設定。行は常に1つだけ持つ（シングルトン）。"""

    site_name = models.CharField("サイト名", max_length=100, default="KururuCMS")
    tagline = models.CharField("キャッチコピー", max_length=200, blank=True, default="")
    description = models.CharField(
        "サイト説明", max_length=160, blank=True, default="",
        help_text="検索結果に出る既定の説明文。160文字以内。",
    )

    # 絶対URLの組み立てに使う。RSS・サイトマップ・OGP は相対URLでは正しく動かない。
    base_url = models.URLField(
        "サイトのURL",
        default="http://localhost:8000",
        help_text="末尾のスラッシュなし。例: https://cms.example.com",
    )

    default_og_image = models.ForeignKey(
        "media_library.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="既定のOG画像",
    )

    accent_color = models.CharField(
        "アクセント色", max_length=7, default="#2563eb", validators=[HEX_COLOR]
    )
    accent_color_dark = models.CharField(
        "アクセント色（ダーク）", max_length=7, default="#60a5fa", validators=[HEX_COLOR]
    )
    theme_key = models.CharField(
        "テーマ",
        max_length=40,
        default=DEFAULT_THEME_KEY,
        validators=[validate_theme_key],
    )
    enable_motion = models.BooleanField(
        "アニメーションを有効にする",
        default=False,
        help_text="利用者の「動きを減らす」設定は常に優先されます。",
    )

    show_sidebar = models.BooleanField("サイドバーを表示", default=True)
    sidebar_recent_count = models.PositiveSmallIntegerField("最新記事の表示数", default=5)

    # 検索エンジンにサイト全体をインデックスさせない（ステージング用）。
    noindex_site = models.BooleanField(
        "サイト全体を検索エンジンから除外", default=False,
        help_text="ステージング環境では必ず有効にする。",
    )

    twitter_site = models.CharField(
        "X（Twitter）のアカウント", max_length=50, blank=True, default="",
        help_text="@ を含めて入力。例: @example",
    )

    updated_at = models.DateTimeField("更新日時", auto_now=True)

    class Meta:
        verbose_name = "サイト設定"
        verbose_name_plural = "サイト設定"

    def __str__(self) -> str:
        return self.site_name

    def save(self, *args, **kwargs):
        # 常に pk=1 に固定する。行が増えると「どれが本物か」が分からなくなる。
        self.pk = 1
        # URL の末尾スラッシュを落として、二重スラッシュを防ぐ。
        self.base_url = (self.base_url or "").rstrip("/")
        super().save(*args, **kwargs)
        self._sync_django_site()

    def _sync_django_site(self) -> None:
        """django.contrib.sites の Site をこの設定に合わせる。

        allauth が送るメールは Site の名前とドメインを使う。
        同期しないと、確認メールの差出人や本文が
        「example.com」（Django の初期値）のままになる。

        本番で気づいたときには、その文面のメールが既に配信済みになる。
        設定を1か所にまとめて、忘れようがない形にしておく。
        """
        from urllib.parse import urlsplit

        try:
            from django.contrib.sites.models import Site
        except Exception:  # sites を外している構成
            return

        netloc = urlsplit(self.base_url).netloc
        if not netloc:
            return

        site_id = getattr(settings, "SITE_ID", 1)
        Site.objects.update_or_create(
            pk=site_id, defaults={"domain": netloc, "name": self.site_name}
        )
        # get_current() はキャッシュを持つので、明示的に捨てる。
        Site.objects.clear_cache()

    def delete(self, *args, **kwargs):
        raise ValidationError("サイト設定は削除できません。")

    @classmethod
    def load(cls) -> "SiteSetting":
        """設定を取得する。無ければ既定値で作る。"""
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def absolute_url(self, path: str) -> str:
        """相対パスをサイトの絶対URLへ変換する。"""
        if not path:
            return self.base_url
        if path.startswith(("http://", "https://")):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"
