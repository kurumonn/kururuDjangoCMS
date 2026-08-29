from django.db import models


class PluginActivation(models.Model):
    key = models.SlugField("プラグインキー", max_length=80, unique=True)
    enabled = models.BooleanField("有効", default=False)
    config = models.JSONField("設定", default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "プラグイン有効化"
        verbose_name_plural = "プラグイン有効化"

    def __str__(self):
        return self.key


def is_plugin_enabled(key: str) -> bool:
    from django.db import OperationalError, ProgrammingError

    try:
        return PluginActivation.objects.filter(key=key, enabled=True).exists()
    except (OperationalError, ProgrammingError):
        return False
