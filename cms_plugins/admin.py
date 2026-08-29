from django.contrib import admin
from django.core.exceptions import ValidationError

from .models import PluginActivation
from .registry import definitions


@admin.register(PluginActivation)
class PluginActivationAdmin(admin.ModelAdmin):
    list_display = ("key", "enabled", "updated_at")
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
