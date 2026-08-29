from django.contrib import admin
from django.core.exceptions import ValidationError
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from .models import PluginActivation
from .registry import definitions


@admin.register(PluginActivation)
class PluginActivationAdmin(admin.ModelAdmin):
    list_display = ("key", "enabled", "management", "updated_at")
    readonly_fields = ("key", "updated_at")
    actions = None

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        installed = {item.key for item in definitions()}
        if obj.key not in installed:
            raise ValidationError("デプロイ時に許可されたプラグインではありません。")
        super().save_model(request, obj, form, change)

    @admin.display(description="管理画面")
    def management(self, obj):
        if not obj.enabled:
            return "有効化後に利用できます"
        definition = next((item for item in definitions() if item.key == obj.key), None)
        if definition is None or not definition.management_url_name:
            return "—"
        try:
            url = reverse(f"{definition.key}:{definition.management_url_name}")
        except NoReverseMatch:
            return "設定エラー"
        return format_html('<a href="{}">開く</a>', url)
