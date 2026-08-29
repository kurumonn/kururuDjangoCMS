"""HTTP 応答へブラウザー向けの防御を追加するミドルウェア。"""

from __future__ import annotations

import secrets

from django.conf import settings


class ContentSecurityPolicyMiddleware:
    """HTMLへリクエスト単位のnonceを使うCSPを付ける。"""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(18)
        response = self.get_response(request)

        content_type = response.headers.get("Content-Type", "").lower()
        if not content_type.startswith("text/html"):
            return response

        directives = [
            "default-src 'self'",
            "base-uri 'none'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "img-src 'self' data:",
            "font-src 'self'",
            "connect-src 'self'",
            "media-src 'self'",
        ]

        admin_prefix = f"/{settings.ADMIN_URL_PATH.strip('/')}/"
        if request.path.startswith(admin_prefix):
            # Django admin 自身がインライン script/style を使う。
            # 管理画面以外へこの互換設定を広げない。
            directives.extend(
                (
                    "script-src 'self' 'unsafe-inline'",
                    "style-src 'self' 'unsafe-inline'",
                )
            )
        else:
            nonce_source = f"'nonce-{request.csp_nonce}'"
            directives.extend(
                (
                    f"script-src 'self' {nonce_source}",
                    f"style-src 'self' {nonce_source}",
                )
            )

        response.headers["Content-Security-Policy"] = "; ".join(directives)
        return response
