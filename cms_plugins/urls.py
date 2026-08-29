from importlib import import_module

from django.urls import include, path
from django.urls.resolvers import URLPattern, URLResolver

from .registry import definitions
from .security import require_plugin_enabled


def _guard_pattern(item, plugin_key):
    if isinstance(item, URLPattern):
        return URLPattern(
            item.pattern,
            require_plugin_enabled(plugin_key)(item.callback),
            item.default_args,
            item.name,
        )
    if isinstance(item, URLResolver):
        return URLResolver(
            item.pattern,
            [_guard_pattern(child, plugin_key) for child in item.url_patterns],
            item.default_kwargs,
            item.app_name,
            item.namespace,
        )
    raise TypeError(f"未対応のURLパターンです: {type(item)!r}")


def guarded_urlpatterns(urlconf, plugin_key):
    module = import_module(urlconf)
    return [_guard_pattern(item, plugin_key) for item in module.urlpatterns]


def _build_urlpatterns():
    result = []
    for plugin in definitions():
        if not plugin.urlconf:
            continue
        guarded = guarded_urlpatterns(plugin.urlconf, plugin.key)
        result.append(
            path(
                plugin.url_prefix.strip("/") + "/",
                include((guarded, plugin.key), namespace=plugin.key),
            )
        )
    return result


urlpatterns = _build_urlpatterns()
