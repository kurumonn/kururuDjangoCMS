from django.apps import AppConfig


class SeoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "seo"
    verbose_name = "SEO・サイト設定"

    def ready(self):
        from . import checks  # noqa: F401
