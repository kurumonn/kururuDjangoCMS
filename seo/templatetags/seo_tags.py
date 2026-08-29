"""SEO 用のテンプレートタグ。

構造化データ（JSON-LD）は、テンプレートに手書きしない。

理由:

  * タイトルや説明文に ``</script>`` という文字列が入っただけで、
    ブラウザがそこをスクリプトの終わりと解釈し、以降の HTML が壊れる。
  * 引用符・改行・バックスラッシュのエスケープを手作業で正しく続けるのは無理がある。
  * テンプレート内で翻訳関数などを呼ぶと、出力が JSON として不正になり、
    Google Search Console に構造化データのエラーが並ぶ。

そこで Python 側で dict を組み立て、``json.dumps`` に任せる。
そのうえで ``<`` を Unicode エスケープし、``</script>`` が
生の形で出力されないようにする。
"""

from __future__ import annotations

import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


def _dump_json_ld(data: dict) -> str:
    """dict を <script> の中へ安全に埋め込める JSON 文字列にする。"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # "<" を \u003C に置き換えると、"</script>" が生成されなくなる。
    # JSON としては同じ文字列を表すため、意味は変わらない。
    payload = payload.replace("<", "\\u003C")
    # 行区切り文字は JavaScript の文法上そのままでは書けない。
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return payload


@register.simple_tag(takes_context=True)
def article_json_ld(context, article) -> str:
    """記事の BlogPosting 構造化データを出力する。"""
    setting = context.get("site_setting")
    if setting is None:
        from seo.models import SiteSetting

        setting = SiteSetting.load()

    canonical_url = article.canonical_url or setting.absolute_url(
        article.get_absolute_url()
    )
    data = {
        "@context": "https://schema.org",
        "@type": "BlogPosting",
        "headline": article.display_seo_title,
        "description": article.display_seo_description,
        "url": canonical_url,
        "mainEntityOfPage": {
            "@type": "WebPage",
            "@id": canonical_url,
        },
        "author": {"@type": "Person", "name": article.author.byline},
        "publisher": {"@type": "Organization", "name": setting.site_name},
        "inLanguage": "ja",
    }

    if article.published_at:
        data["datePublished"] = article.published_at.isoformat()
    data["dateModified"] = article.updated_at.isoformat()

    image = article.display_og_image
    if image:
        data["image"] = [setting.absolute_url(image.file.url)]

    keywords = [article.category.name] + [tag.name for tag in article.tags.all()]
    if keywords:
        data["keywords"] = ", ".join(keywords)

    # _dump_json_ld escapes '<', including every possible closing script tag.
    return mark_safe(_dump_json_ld(data))  # nosec B308 B703


@register.simple_tag(takes_context=True)
def page_json_ld(context, page) -> str:
    """固定ページのWebPage構造化データを安全なJSONとして返す。"""
    setting = context.get("site_setting")
    if setting is None:
        from seo.models import SiteSetting

        setting = SiteSetting.load()

    canonical_url = page.canonical_url or setting.absolute_url(page.get_absolute_url())
    data = {
        "@context": "https://schema.org",
        "@type": "WebPage",
        "name": page.display_seo_title,
        "description": page.display_seo_description,
        "url": canonical_url,
        "inLanguage": "ja",
        "dateModified": page.updated_at.isoformat(),
    }
    if page.published_at:
        data["datePublished"] = page.published_at.isoformat()
    if page.display_og_image:
        data["primaryImageOfPage"] = {
            "@type": "ImageObject",
            "url": setting.absolute_url(page.display_og_image.file.url),
        }
    # _dump_json_ld escapes '<', including every possible closing script tag.
    return mark_safe(_dump_json_ld(data))  # nosec B308 B703


@register.simple_tag(takes_context=True)
def breadcrumb_json_ld(context, crumbs) -> str:
    """パンくずリストの構造化データ。

    crumbs は [(表示名, パス), ...] のリスト。
    """
    setting = context.get("site_setting")
    if setting is None:
        from seo.models import SiteSetting

        setting = SiteSetting.load()

    data = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": name,
                "item": setting.absolute_url(path),
            }
            for index, (name, path) in enumerate(crumbs, start=1)
        ],
    }
    # _dump_json_ld escapes '<', including every possible closing script tag.
    return mark_safe(_dump_json_ld(data))  # nosec B308 B703


@register.simple_tag(takes_context=True)
def absolute_url(context, path: str) -> str:
    """相対パスをサイトの絶対URLへ変換する（OGP 用）。"""
    setting = context.get("site_setting")
    if setting is None:
        from seo.models import SiteSetting

        setting = SiteSetting.load()
    return setting.absolute_url(path)
