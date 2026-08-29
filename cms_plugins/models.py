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
    return key in enabled_plugin_keys({key})


def enabled_plugin_keys(keys) -> set[str]:
    """有効状態を一括取得し、ブロック数に比例するDB問い合わせを避ける。"""
    from django.db import OperationalError, ProgrammingError

    keys = {key for key in keys if key}
    if not keys:
        return set()
    try:
        return set(
            PluginActivation.objects.filter(key__in=keys, enabled=True).values_list(
                "key", flat=True
            )
        )
    except (OperationalError, ProgrammingError):
        return set()
