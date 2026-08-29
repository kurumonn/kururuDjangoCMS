from importlib.metadata import EntryPoint
from unittest.mock import patch

from django.apps import AppConfig
from django.test import SimpleTestCase

from cms_plugins.discovery import discover_plugin_apps


class FakePluginConfig(AppConfig):
    name = "cms_plugins"


class NotAppConfig:
    pass


class DiscoveryTests(SimpleTestCase):
    def entries(self, *items):
        class Entries(list):
            def select(self, **kwargs):
                return [x for x in self if x.group == kwargs["group"]]
        return Entries(items)

    def test_only_explicitly_allowed_entry_points_are_loaded(self):
        entries = self.entries(
            EntryPoint(
                "forms",
                "cms_plugins.tests.test_discovery:FakePluginConfig",
                "kururucms.plugins",
            ),
            EntryPoint(
                "evil",
                "cms_plugins.tests.test_discovery:NotAppConfig",
                "kururucms.plugins",
            ),
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            self.assertEqual(
                discover_plugin_apps(["forms"]),
                ["cms_plugins.tests.test_discovery.FakePluginConfig"],
            )

    def test_missing_allowed_plugin_fails_closed(self):
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=self.entries()):
            with self.assertRaises(RuntimeError):
                discover_plugin_apps(["missing"])

    def test_entry_point_must_resolve_to_app_config(self):
        entries = self.entries(
            EntryPoint(
                "forms",
                "cms_plugins.tests.test_discovery:NotAppConfig",
                "kururucms.plugins",
            )
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            with self.assertRaises(RuntimeError):
                discover_plugin_apps(["forms"])

    def test_legacy_dotted_entry_point_is_rejected(self):
        entries = self.entries(
            EntryPoint(
                "forms",
                "cms_plugins.tests.test_discovery.FakePluginConfig",
                "kururucms.plugins",
            )
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            with self.assertRaises(RuntimeError):
                discover_plugin_apps(["forms"])

    def test_duplicate_unallowed_entry_points_do_not_break_startup(self):
        entries = self.entries(
            EntryPoint("unused", "one.apps:OneConfig", "kururucms.plugins"),
            EntryPoint("unused", "two.apps:TwoConfig", "kururucms.plugins"),
            EntryPoint(
                "forms",
                "cms_plugins.tests.test_discovery:FakePluginConfig",
                "kururucms.plugins",
            ),
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            self.assertEqual(
                discover_plugin_apps(["forms"]),
                ["cms_plugins.tests.test_discovery.FakePluginConfig"],
            )
