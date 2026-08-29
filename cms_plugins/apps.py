from django.apps import AppConfig


class CmsPluginsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "cms_plugins"
    verbose_name = "CMSプラグイン"

    def ready(self):
        from . import checks  # noqa: F401
        from django.db.models.signals import post_migrate

        from .models import PluginActivation
        from .registry import definitions

        def sync_installed_plugins(**kwargs):
            for plugin in definitions():
                PluginActivation.objects.get_or_create(key=plugin.key)

        post_migrate.connect(
            sync_installed_plugins,
            dispatch_uid="cms_plugins.sync_installed_plugins",
            weak=False,
        )
