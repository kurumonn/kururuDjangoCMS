from django.http import Http404
from django.test import RequestFactory, TestCase

from cms_plugins.models import PluginActivation
from cms_plugins.urls import guarded_urlpatterns


class PluginUrlGuardTests(TestCase):
    def test_host_denies_unguarded_plugin_view_when_disabled(self):
        PluginActivation.objects.update_or_create(
            key="fake_plugin", defaults={"enabled": False}
        )
        callback = guarded_urlpatterns(
            "cms_plugins.tests.fake_urls", "fake_plugin"
        )[0].callback
        with self.assertRaises(Http404):
            callback(RequestFactory().get("/fake/action/"))

    def test_host_allows_plugin_view_when_enabled(self):
        PluginActivation.objects.update_or_create(
            key="fake_plugin", defaults={"enabled": True}
        )
        callback = guarded_urlpatterns(
            "cms_plugins.tests.fake_urls", "fake_plugin"
        )[0].callback
        self.assertEqual(
            callback(RequestFactory().get("/fake/action/")).status_code,
            200,
        )
