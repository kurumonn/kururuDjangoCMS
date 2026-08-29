from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied

from .models import SiteSetting
from .themes import theme_choices


class SiteSettingAdminForm(forms.ModelForm):
    theme_key = forms.ChoiceField(label="テーマ", choices=theme_choices)

    class Meta:
        model = SiteSetting
        fields = "__all__"


@admin.register(SiteSetting)
class SiteSettingAdmin(admin.ModelAdmin):
    """設定は1行だけ。追加・削除の口を塞ぐ。"""
    form = SiteSettingAdminForm

    fieldsets = (
        ("サイト情報", {"fields": ("site_name", "tagline", "description", "base_url")}),
        ("SNS・共有", {"fields": ("default_og_image", "twitter_site")}),
        ("見た目", {
            "fields": (
                "accent_color",
                "accent_color_dark",
                "theme_key",
                "enable_motion",
                "show_sidebar",
                "sidebar_recent_count",
            )
        }),
        ("検索エンジン", {"fields": ("noindex_site",)}),
    )

    def has_add_permission(self, request):
        # すでに1行あるなら追加させない。
        return super().has_add_permission(request) and not SiteSetting.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        """一覧を出さず、いきなり唯一の設定の編集画面へ送る。"""
        from django.shortcuts import redirect
        from django.urls import reverse

        setting = SiteSetting.objects.first()
        if setting is None:
            if self.has_add_permission(request):
                return redirect(reverse("admin:seo_sitesetting_add"))
            raise PermissionDenied("サイト設定を追加する権限がありません。")
        return redirect(
            reverse("admin:seo_sitesetting_change", args=[setting.pk])
        )
