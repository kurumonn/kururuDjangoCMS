"""すべてのテンプレートへサイト設定とサイドバーの内容を渡す。"""

from __future__ import annotations

from django.core.cache import cache

from .contrast import readable_foreground
from .models import SiteSetting
from .themes import resolve_theme

SIDEBAR_CACHE_KEY = "seo:sidebar"
SIDEBAR_CACHE_SECONDS = 60


def get_site_setting(request) -> SiteSetting:
    """1リクエスト中に1回だけ設定を読む。

    コンテキストプロセッサは複数あり、どれも設定を必要とする。
    それぞれが素直に load() を呼ぶと、1ページあたり同じ SELECT が何度も走る。
    リクエストオブジェクトへ覚えさせて、読み込みを1回に抑える。

    グローバルなキャッシュにしないのは、設定変更の反映が遅れる問題と、
    キャッシュ破棄の書き忘れを避けるため。リクエスト内だけなら失効を考えずに済む。
    """
    cached = getattr(request, "_site_setting", None)
    if cached is None:
        cached = SiteSetting.load()
        request._site_setting = cached
    return cached


def site_settings(request):
    """サイト設定。"""
    setting = get_site_setting(request)
    return {
        "site_setting": setting,
        "active_theme": resolve_theme(setting.theme_key),
        "accent_foreground": readable_foreground(setting.accent_color),
        "accent_foreground_dark": readable_foreground(setting.accent_color_dark),
    }


def sidebar(request):
    """サイドバーの内容（最新記事・カテゴリ・タグ）。

    全ページで同じクエリが走るため、短時間だけキャッシュする。
    キャッシュ時間を長くしすぎると、新着記事がサイドバーに出ない時間が伸びる。
    """
    setting = get_site_setting(request)
    if not setting.show_sidebar:
        return {"sidebar": None}

    cache_key = f"{SIDEBAR_CACHE_KEY}:{setting.sidebar_recent_count}"
    cached = cache.get(cache_key)
    if cached is not None:
        return {"sidebar": cached}

    from blog.models import Article, Category, Tag

    data = {
        "recent_articles": list(
            Article.objects.published().with_related()[: setting.sidebar_recent_count]
        ),
        "categories": list(Category.objects.all()[:20]),
        "tags": list(Tag.objects.all()[:30]),
    }
    cache.set(cache_key, data, SIDEBAR_CACHE_SECONDS)
    return {"sidebar": data}
