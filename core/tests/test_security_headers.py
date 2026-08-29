"""ブラウザー向け防御ヘッダーの回帰テスト。"""

import re

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from core.middleware import ContentSecurityPolicyMiddleware


class ContentSecurityPolicyMiddlewareTests(SimpleTestCase):
    def test_html_response_gets_per_response_nonce_and_strict_policy(self):
        def view(request):
            return HttpResponse(
                f'<style nonce="{request.csp_nonce}">:root {{}}</style>'
            )

        response = ContentSecurityPolicyMiddleware(view)(RequestFactory().get("/"))
        policy = response.headers["Content-Security-Policy"]

        self.assertIn("default-src 'self'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertNotIn("'unsafe-inline'", policy)
        nonce = re.search(r"script-src 'self' 'nonce-([^']+)'", policy).group(1)
        self.assertContains(response, f'nonce="{nonce}"')

    def test_admin_compatibility_does_not_weaken_other_routes(self):
        middleware = ContentSecurityPolicyMiddleware(
            lambda request: HttpResponse("<html></html>")
        )

        admin_policy = middleware(
            RequestFactory().get("/admin/login/")
        ).headers["Content-Security-Policy"]
        public_policy = middleware(
            RequestFactory().get("/articles/example/")
        ).headers["Content-Security-Policy"]

        self.assertIn("'unsafe-inline'", admin_policy)
        self.assertNotIn("'unsafe-inline'", public_policy)

    def test_non_html_response_is_not_given_a_csp_header(self):
        middleware = ContentSecurityPolicyMiddleware(
            lambda request: HttpResponse(
                b"data", content_type="application/octet-stream"
            )
        )

        response = middleware(RequestFactory().get("/download"))

        self.assertNotIn("Content-Security-Policy", response.headers)
