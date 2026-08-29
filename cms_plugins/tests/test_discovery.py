from importlib.metadata import EntryPoint
from unittest.mock import patch

from django.test import SimpleTestCase

from cms_plugins.discovery import discover_plugin_apps


class DiscoveryTests(SimpleTestCase):
    def entries(self, *items):
        class Entries(list):
            def select(self, **kwargs):
                return [x for x in self if x.group == kwargs["group"]]
        return Entries(items)

    def test_only_explicitly_allowed_entry_points_are_loaded(self):
        entries = self.entries(
            EntryPoint("forms", "contact_forms.apps.ContactFormsConfig", "kururucms.plugins"),
            EntryPoint("evil", "evil.apps.EvilConfig", "kururucms.plugins"),
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            self.assertEqual(discover_plugin_apps(["forms"]), ["contact_forms.apps.ContactFormsConfig"])

    def test_missing_allowed_plugin_fails_closed(self):
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=self.entries()):
            with self.assertRaises(RuntimeError):
                discover_plugin_apps(["missing"])

    def test_entry_point_cannot_call_factory_or_use_extras(self):
        entries = self.entries(EntryPoint("forms", "bad.module:factory", "kururucms.plugins"))
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            with self.assertRaises(RuntimeError):
                discover_plugin_apps(["forms"])

    def test_duplicate_unallowed_entry_points_do_not_break_startup(self):
        entries = self.entries(
            EntryPoint("unused", "one.apps.OneConfig", "kururucms.plugins"),
            EntryPoint("unused", "two.apps.TwoConfig", "kururucms.plugins"),
            EntryPoint("forms", "contact_forms.apps.ContactFormsConfig", "kururucms.plugins"),
        )
        with patch("cms_plugins.discovery.metadata.entry_points", return_value=entries):
            self.assertEqual(
                discover_plugin_apps(["forms"]),
                ["contact_forms.apps.ContactFormsConfig"],
            )
