from django.contrib.staticfiles import finders
from django.core.checks import Error, register

from .themes import THEMES


@register()
def theme_static_files_check(app_configs, **kwargs):
    errors = []
    for theme in THEMES:
        if finders.find(theme.css_path) is None:
            errors.append(
                Error(
                    f"テーマCSSが見つかりません: {theme.css_path}",
                    id="seo.E001",
                )
            )
    return errors
