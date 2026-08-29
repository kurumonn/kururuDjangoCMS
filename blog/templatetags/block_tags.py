"""ブロックを HTML へ描画するテンプレートタグ。

描画の方針:

  * ブロックの種類ごとにテンプレートを1つ用意する
  * 値は必ず Django のエスケープを通す（`|safe` を一切使わない）
  * 未知の種類は描画しない（保存時に弾いているが、二重に守る）

「投稿者が書いた HTML をそのまま出す」構造を作らないことが要点。
出力する HTML はすべてこちらのテンプレートに書かれているので、
投稿内容がどれだけ汚染されていてもタグとしては解釈されない。
"""

from __future__ import annotations

from django import template
from django.template.loader import render_to_string

register = template.Library()

# ブロック種別 -> テンプレート名
TEMPLATES = {
    "heading": "blog/blocks/heading.html",
    "paragraph": "blog/blocks/paragraph.html",
    "image": "blog/blocks/image.html",
    "code": "blog/blocks/code.html",
    "quote": "blog/blocks/quote.html",
    "table": "blog/blocks/table.html",
    "note": "blog/blocks/note.html",
    "cta": "blog/blocks/cta.html",
    "related": "blog/blocks/related.html",
}

# 再利用ブロックの入れ子は1段までにする。
# 制限しないと、互いを参照し合う2つの再利用ブロックで無限ループになる。
MAX_REUSABLE_DEPTH = 1


@register.simple_tag(takes_context=True)
def render_blocks(context, blocks, _depth: int = 0):
    """ブロックの配列を HTML へ変換する。"""
    if not blocks:
        return ""

    # 参照されるオブジェクトをまとめて引く（ブロックごとに引くと N+1 になる）。
    media_map = _load_media(blocks)
    article_map = _load_articles(blocks)
    reusable_map = _load_reusables(blocks) if _depth < MAX_REUSABLE_DEPTH else {}

    from cms_plugins.models import enabled_plugin_keys
    from cms_plugins.registry import plugin_block

    registered_blocks = {
        block.get("type"): plugin_block(block.get("type"))
        for block in blocks
        if block.get("type") not in TEMPLATES and block.get("type") != "reusable"
    }
    enabled_keys = enabled_plugin_keys(
        registered[0] for registered in registered_blocks.values() if registered
    )

    parts: list[str] = []
    for block in blocks:
        block_type = block.get("type")
        data = block.get("data", {})

        if block_type == "reusable":
            reusable = reusable_map.get(data.get("reusable_id"))
            if reusable is not None:
                parts.append(render_blocks(context, reusable.blocks, _depth + 1))
            continue

        template_name = TEMPLATES.get(block_type)
        plugin_context = {}
        if template_name is None:
            registered = registered_blocks.get(block_type)
            if registered and registered[0] in enabled_keys:
                plugin = registered[1]
                template_name = plugin.template_name
                if plugin.context_provider:
                    plugin_context = plugin.context_provider(context.get("request"), data)
        if template_name is None:
            # 未知の種類は黙って捨てる。ここまで来るのは、
            # 保存後にブロック種別を削除した場合など。
            continue

        parts.append(
            render_to_string(
                template_name,
                {
                    "data": data,
                    "media": media_map.get(data.get("media_id")),
                    "articles": [
                        article_map[i]
                        for i in data.get("article_ids", [])
                        if i in article_map
                    ],
                    **plugin_context,
                },
                request=context.get("request"),
            )
        )

    from django.utils.safestring import mark_safe

    # 各テンプレートの出力はエスケープ済み。連結だけを安全とみなす。
    # render_to_string済みの断片だけを連結する。ユーザー値を直接連結しない。
    return mark_safe("".join(parts))  # nosec B308 B703


def _load_media(blocks) -> dict:
    ids = [
        b["data"]["media_id"]
        for b in blocks
        if b.get("type") == "image" and isinstance(b.get("data"), dict)
        and isinstance(b["data"].get("media_id"), int)
    ]
    if not ids:
        return {}
    from media_library.models import MediaAsset

    return {m.pk: m for m in MediaAsset.objects.filter(pk__in=ids)}


def _load_articles(blocks) -> dict:
    ids: list[int] = []
    for b in blocks:
        if b.get("type") == "related" and isinstance(b.get("data"), dict):
            ids.extend(i for i in b["data"].get("article_ids", []) if isinstance(i, int))
    if not ids:
        return {}
    from blog.models import Article

    # 未公開の記事を関連記事として出さない。
    return {
        a.pk: a
        for a in Article.objects.published().with_related().filter(pk__in=ids)
    }


def _load_reusables(blocks) -> dict:
    ids = [
        b["data"]["reusable_id"]
        for b in blocks
        if b.get("type") == "reusable" and isinstance(b.get("data"), dict)
        and isinstance(b["data"].get("reusable_id"), int)
    ]
    if not ids:
        return {}
    from blog.models import ReusableBlock

    return {r.pk: r for r in ReusableBlock.objects.filter(pk__in=ids)}
