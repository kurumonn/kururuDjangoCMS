"""SEO URLをすべての保存経路で共通検証する。"""

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError


def validate_canonical_url(value: str) -> None:
    if not value:
        return
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("正規URLは完全なHTTP(S) URLで指定してください。")
    if parsed.username or parsed.password:
        raise ValidationError("認証情報を含む正規URLは指定できません。")
    if parsed.fragment:
        raise ValidationError("正規URLにフラグメントは指定できません。")
