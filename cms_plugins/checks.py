from django.core.checks import Error, register
from django.db import OperationalError, ProgrammingError

from .models import PluginActivation
from .registry import definitions


@register()
def check_plugin_activations(app_configs, **kwargs):
    installed = {item.key for item in definitions()}
    try:
        unknown = list(
            PluginActivation.objects.filter(enabled=True)
            .exclude(key__in=installed)
            .values_list("key", flat=True)
        )
    except (OperationalError, ProgrammingError):
        return []
    return [
        Error(
            f"有効化DBに未知のプラグインがあります: {key}",
            hint="無効化するか、許可済みパッケージをデプロイしてください。",
            id="cms_plugins.E001",
        )
        for key in unknown
    ]
