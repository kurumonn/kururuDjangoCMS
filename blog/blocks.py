"""ブロックエディターのデータ構造と検証。

本文を1つの巨大な HTML 文字列として保存すると、次の問題が起きる。

  * 投稿者が任意の HTML を書けるため、`<script>` を止めきれない
  * 「見出しだけ抜き出して目次を作る」といった加工ができない
  * デザインを変えるたびに、過去記事の HTML を一括置換することになる

そこで本文を「ブロックの配列」として持つ。
保存されるのは意味（見出し・段落・画像）であって、見た目ではない。
HTML はテンプレート側で組み立てるので、出力は常にこちらの管理下に入る。

    [
      {"type": "heading",   "data": {"level": 2, "text": "見出し"}},
      {"type": "paragraph", "data": {"text": "本文"}},
      {"type": "image",     "data": {"media_id": 15, "alt": "説明"}}
    ]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from django.core.exceptions import ValidationError

# 1記事あたりのブロック数の上限。
# 上限が無いと、巨大な JSON を送りつけるだけでメモリと描画時間を奪える。
MAX_BLOCKS = 300

# 1つのテキストの上限（文字数）。
MAX_TEXT_LENGTH = 20_000


@dataclass(frozen=True)
class BlockType:
    name: str
    label: str
    validate: Callable[[dict], dict]
    editor_fields: tuple[dict, ...] = ()


def _text(data: dict, key: str = "text", *, required: bool = True) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValidationError(f"{key} は文字列で指定してください。")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{key} が空です。")
    if len(value) > MAX_TEXT_LENGTH:
        raise ValidationError(f"{key} が長すぎます（上限 {MAX_TEXT_LENGTH} 文字）。")
    return value


def _validate_heading(data: dict) -> dict:
    level = data.get("level", 2)
    if level not in (2, 3, 4):
        # h1 は記事タイトルが使う。見出しレベルを飛ばさせないため h2〜h4 に限る。
        raise ValidationError("見出しレベルは 2〜4 で指定してください。")
    return {"level": int(level), "text": _text(data)}


def _validate_paragraph(data: dict) -> dict:
    return {"text": _text(data)}


def _validate_image(data: dict) -> dict:
    media_id = data.get("media_id")
    if not isinstance(media_id, int) or media_id <= 0:
        raise ValidationError("画像は メディアID（正の整数）で指定してください。")
    return {
        "media_id": media_id,
        "alt": _text(data, "alt", required=False),
        "caption": _text(data, "caption", required=False),
    }


def _validate_code(data: dict) -> dict:
    language = data.get("language", "")
    if not isinstance(language, str) or len(language) > 30:
        raise ValidationError("言語名が不正です。")
    # 言語名はそのまま CSS クラスへ入れるので、英数字とハイフンだけに限る。
    if language and not language.replace("-", "").replace("+", "").isalnum():
        raise ValidationError("言語名には英数字とハイフンだけが使えます。")
    return {"language": language.lower(), "code": _text(data, "code")}


def _validate_quote(data: dict) -> dict:
    return {"text": _text(data), "cite": _text(data, "cite", required=False)}


def _validate_note(data: dict) -> dict:
    variant = data.get("variant", "info")
    if variant not in ("info", "warning", "danger"):
        raise ValidationError("注意書きの種類が不正です。")
    return {"variant": variant, "text": _text(data)}


def _validate_table(data: dict) -> dict:
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValidationError("表には少なくとも1行必要です。")
    if len(rows) > 200:
        raise ValidationError("表の行数が多すぎます（上限 200 行）。")

    normalized = []
    width = None
    for row in rows:
        if not isinstance(row, list):
            raise ValidationError("表の各行は配列で指定してください。")
        if len(row) > 20:
            raise ValidationError("表の列数が多すぎます（上限 20 列）。")
        if width is None:
            width = len(row)
        elif len(row) != width:
            raise ValidationError("表の列数が行によって違います。")
        normalized.append([_text({"text": cell}, required=False) for cell in row])

    return {"has_header": bool(data.get("has_header", True)), "rows": normalized}


def _validate_cta(data: dict) -> dict:
    url = _text(data, "url")
    # javascript: や data: を弾く。リンク先は http(s) と相対パスだけ許可する。
    if not (url.startswith(("https://", "http://", "/"))):
        raise ValidationError("リンク先は http(s) か / で始まるパスにしてください。")
    return {"text": _text(data), "url": url}


def _validate_related(data: dict) -> dict:
    ids = data.get("article_ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, int) and i > 0 for i in ids):
        raise ValidationError("関連記事は記事IDの配列で指定してください。")
    if len(ids) > 10:
        raise ValidationError("関連記事は10件までです。")
    return {"article_ids": ids}


def _validate_reusable(data: dict) -> dict:
    block_id = data.get("reusable_id")
    if not isinstance(block_id, int) or block_id <= 0:
        raise ValidationError("再利用ブロックのIDが不正です。")
    return {"reusable_id": block_id}


BLOCK_TYPES: dict[str, BlockType] = {
    b.name: b
    for b in [
        BlockType("heading", "見出し", _validate_heading, (
            {"key": "level", "type": "select", "label": "レベル", "options": [2, 3, 4], "value": 2},
            {"key": "text", "type": "text", "label": "テキスト"},
        )),
        BlockType("paragraph", "段落", _validate_paragraph, (
            {"key": "text", "type": "textarea", "label": "本文"},
        )),
        BlockType("image", "画像", _validate_image, (
            {"key": "media_id", "type": "number", "label": "メディアID"},
            {"key": "alt", "type": "text", "label": "代替テキスト"},
            {"key": "caption", "type": "text", "label": "キャプション"},
        )),
        BlockType("code", "コード", _validate_code, (
            {"key": "language", "type": "text", "label": "言語（python など）"},
            {"key": "code", "type": "textarea", "label": "コード"},
        )),
        BlockType("quote", "引用", _validate_quote, (
            {"key": "text", "type": "textarea", "label": "引用文"},
            {"key": "cite", "type": "text", "label": "出典"},
        )),
        BlockType("table", "表", _validate_table),
        BlockType("note", "注意書き", _validate_note, (
            {"key": "variant", "type": "select", "label": "種類", "options": ["info", "warning", "danger"], "value": "info"},
            {"key": "text", "type": "textarea", "label": "本文"},
        )),
        BlockType("cta", "行動喚起", _validate_cta, (
            {"key": "text", "type": "text", "label": "ボタンの文言"},
            {"key": "url", "type": "text", "label": "リンク先（https:// か / で始める）"},
        )),
        BlockType("related", "関連記事", _validate_related),
        BlockType("reusable", "再利用ブロック", _validate_reusable),
    ]
}


def get_block_type(name: str) -> BlockType | None:
    builtin = BLOCK_TYPES.get(name)
    if builtin is not None:
        return builtin
    from cms_plugins.registry import plugin_block

    registered = plugin_block(name)
    if registered is None:
        return None
    _plugin_key, block = registered
    return BlockType(block.name, block.label, block.validate)


def block_editor_catalog() -> dict[str, dict]:
    catalog = {
        item.name: {"label": item.label, "fields": list(item.editor_fields)}
        for item in BLOCK_TYPES.values()
        if item.editor_fields
    }
    from cms_plugins.models import is_plugin_enabled
    from cms_plugins.registry import definitions

    for plugin in definitions():
        if not is_plugin_enabled(plugin.key):
            continue
        for block in plugin.blocks:
            catalog[block.name] = {
                "label": block.label,
                "fields": [field.as_dict() for field in block.editor_fields],
            }
    return catalog


def validate_blocks(value: Any) -> list[dict]:
    """ブロック配列を検証し、正規化したものを返す。

    未知の種類はエラーにする。黙って無視すると、
    「保存できたのに表示されない」という分かりにくい状態になる。
    """
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValidationError("ブロックは配列で指定してください。")
    if len(value) > MAX_BLOCKS:
        raise ValidationError(f"ブロック数が多すぎます（上限 {MAX_BLOCKS}）。")

    normalized: list[dict] = []
    for index, block in enumerate(value, start=1):
        if not isinstance(block, dict):
            raise ValidationError(f"{index} 番目のブロックが不正です。")

        block_type = block.get("type")
        spec = get_block_type(block_type)
        if spec is None:
            raise ValidationError(f"{index} 番目: 未知のブロック種別「{block_type}」です。")

        data = block.get("data")
        if not isinstance(data, dict):
            raise ValidationError(f"{index} 番目のブロックに data がありません。")

        try:
            normalized.append({"type": spec.name, "data": spec.validate(data)})
        except ValidationError as exc:
            raise ValidationError(f"{index} 番目（{spec.label}）: {exc.messages[0]}") from exc

    return normalized


def blocks_to_plain_text(blocks: list[dict]) -> str:
    """検索と抜粋のために、ブロックから素のテキストを取り出す。

    検索対象を JSON 文字列そのままにすると、
    "heading" や "media_id" といったキー名にヒットしてしまう。
    """
    parts: list[str] = []
    for block in blocks or []:
        data = block.get("data", {})
        block_type = block.get("type")
        if block_type in ("heading", "paragraph", "note", "cta"):
            parts.append(data.get("text", ""))
        elif block_type == "quote":
            parts.append(data.get("text", ""))
            parts.append(data.get("cite", ""))
        elif block_type == "code":
            parts.append(data.get("code", ""))
        elif block_type == "image":
            parts.append(data.get("caption", "") or data.get("alt", ""))
        elif block_type == "table":
            for row in data.get("rows", []):
                parts.extend(row)
        else:
            from cms_plugins.registry import plugin_block

            registered = plugin_block(block_type)
            if registered and registered[1].plain_text_provider:
                parts.append(registered[1].plain_text_provider(data))
    return "\n".join(p for p in parts if p)
