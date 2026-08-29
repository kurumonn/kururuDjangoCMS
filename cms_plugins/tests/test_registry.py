from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from cms_plugins.registry import (
    PluginBlock,
    PluginDefinition,
    clear_registry,
    register_plugin,
)


class RegistryTests(SimpleTestCase):
    def tearDown(self):
        clear_registry()

    def test_incompatible_api_version_is_rejected(self):
        with self.assertRaises(ImproperlyConfigured):
            register_plugin(PluginDefinition("future", "Future", "1", "", api_version=999))

    def test_duplicate_plugin_key_is_rejected(self):
        plugin = PluginDefinition("same", "Same", "1", "")
        register_plugin(plugin)
        with self.assertRaises(ImproperlyConfigured):
            register_plugin(plugin)

    def test_block_name_must_be_namespaced_to_plugin(self):
        block = PluginBlock("paragraph", "Bad", lambda data: data, "bad.html", ())
        with self.assertRaises(ImproperlyConfigured):
            register_plugin(
                PluginDefinition("sample", "Sample", "1", "", blocks=(block,))
            )
