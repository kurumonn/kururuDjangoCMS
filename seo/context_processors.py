"""すべてのテンプレートへサイト設定とサイドバーの内容を渡す。"""

from __future__ import annotations

from django.core.cache import cache

from .contrast import ensure_readable_text, readable_foreground
from .models import HEX_COLOR, SiteSetting
from .themes import DARK, resolve_theme

SIDEBAR_CACHE_KEY = "seo:sidebar"
SIDEBAR_CACHE_SECONDS = 60

HEX_COLOR_PATTERN = HEX_COLOR.regex
DEFAULT_ACCENT = "#2563eb"


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


def theme_colors(setting) -> dict[str, str]:
    """テーマの背景に合わせてアクセント色とリンク色を決める。

    アクセント色は2種類（明るい背景用・暗い背景用）を管理画面で持つが、
    どちらを使うかは **利用者のOS設定ではなくテーマの明暗** で決める。
    テーマの背景は固定なので、OS設定で切り替えると背景と色が食い違う。

    さらに、アクセント色は「背景として敷く色」であって、
    そのまま本文中のリンク色に使えるとは限らない。
    リンクはテーマの地色・カード地の上に置く文字なので、
    読める明るさへ寄せた別の色（--link）を用意する。
    """
    theme = resolve_theme(setting.theme_key)
    stored = setting.accent_color_dark if theme.scheme == DARK else setting.accent_color
    # save() は full_clean() を呼ばないので、管理画面を通らない保存
    # （シェル・データ移行・フィクスチャ）では検証済みでない値が入りうる。
    # 色を組み立てられない値で全ページが 500 になるのは割に合わないので、
    # 形式が違うものは既定値へ落とす。theme_key と同じ考え方。
    accent = stored if HEX_COLOR_PATTERN.fullmatch(stored or "") else DEFAULT_ACCENT
    return {
        "active_theme": theme,
        "theme_accent": accent,
        # アクセント色を背景に敷いたときに載せる文字色（ボタンなど）。
        "accent_foreground": readable_foreground(accent),
        # テーマの背景の上に置くリンク文字の色。
        "theme_link": ensure_readable_text(accent, theme.backgrounds),
    }


def site_settings(request):
    """サイト設定。"""
    setting = get_site_setting(request)
    return {"site_setting": setting, **theme_colors(setting)}


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
