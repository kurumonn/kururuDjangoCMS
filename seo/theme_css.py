"""テーマCSSを読み、配色の約束を守っているか検査する。

テーマは「共通テンプレート + CSS変数の契約」で成り立っている。
契約とは、テーマ側が次の変数を **すべて** 定義することを指す。

    color-scheme / --bg / --surface / --fg / --muted / --border / --danger / --success

一部だけを定義すると、残りは共通CSS（site.css）の初期値のまま残る。
実際にテーマ側が `--fg` ではなく `--text` を定義していたため、
背景だけがテーマの色になり、本文色は共通CSSの値が残って
コントラストが 1.1:1 まで落ちていた。ここでの検査はその再発を止めるためのもの。

CSS を実行時に解釈するのではなく、
`manage.py check` とテストから静的に読むだけなので、描画性能には影響しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contrast import NORMAL_TEXT_CONTRAST, contrast_ratio
from .themes import ThemeDefinition

# テーマが必ず定義する CSS 変数。
REQUIRED_VARIABLES = ("--bg", "--surface", "--fg", "--muted", "--border", "--danger", "--success")

# 本文と同じサイズで表示される文字に使う変数。背景に対して 4.5:1 以上が要る。
TEXT_VARIABLES = ("--fg", "--muted", "--danger", "--success")

# 過去に使われていて、共通CSSと噛み合わなかった変数名。
FORBIDDEN_VARIABLES = ("--text",)

_HEX = re.compile(r"^#(?:[0-9a-f]{3}|[0-9a-f]{6})$")


@dataclass(frozen=True)
class ThemeStyle:
    key: str
    scheme: str
    variables: dict[str, str]


def _root_block(css: str, key: str) -> str:
    """`html[data-theme="key"] { ... }` の中身を取り出す。

    テーマCSSは1ファイル1テーマで、先頭のブロックが配色宣言という約束。
    入れ子のない単純な構造なので、最初の `}` までを読めば足りる。
    """
    pattern = re.compile(
        r'html\[data-theme="%s"\]\s*\{([^}]*)\}' % re.escape(key)
    )
    match = pattern.search(css)
    if match is None:
        raise ValueError(f'html[data-theme="{key}"] の宣言ブロックが見つかりません。')
    return match.group(1)


def parse_theme_css(css: str, key: str) -> ThemeStyle:
    block = _root_block(css, key)
    declarations = dict(
        (name.strip(), value.strip())
        for name, value in re.findall(r"(--[a-z-]+)\s*:\s*([^;]+);", block)
    )
    scheme_match = re.search(r"color-scheme\s*:\s*([a-z ]+);", block)
    scheme = scheme_match.group(1).strip() if scheme_match else ""
    return ThemeStyle(key=key, scheme=scheme, variables=declarations)


def audit_theme(theme: ThemeDefinition, css: str) -> list[str]:
    """1テーマ分の CSS を検査し、問題のメッセージを返す（無ければ空）。"""
    try:
        style = parse_theme_css(css, theme.key)
    except ValueError as exc:
        return [str(exc)]

    problems: list[str] = []

    if style.scheme != theme.scheme:
        problems.append(
            f"color-scheme が themes.py と一致しません "
            f"(CSS: {style.scheme or '未指定'} / Python: {theme.scheme})"
        )

    for name in FORBIDDEN_VARIABLES:
        if name in style.variables:
            problems.append(
                f"{name} は共通CSSが参照しない変数名です（--fg を使ってください）。"
            )

    missing = [name for name in REQUIRED_VARIABLES if name not in style.variables]
    if missing:
        problems.append(f"必須のCSS変数が未定義です: {', '.join(missing)}")

    for name, expected in (("--bg", theme.background), ("--surface", theme.surface)):
        actual = style.variables.get(name)
        if actual is not None and actual.lower() != expected.lower():
            problems.append(
                f"{name} が themes.py と一致しません (CSS: {actual} / Python: {expected})"
            )

    for name in REQUIRED_VARIABLES:
        value = style.variables.get(name)
        if value is not None and not _HEX.fullmatch(value.lower()):
            problems.append(
                f"{name} は #rgb か #rrggbb で指定してください（半透明色は"
                f"コントラストを計算できません）: {value}"
            )

    backgrounds = [
        style.variables.get("--bg", theme.background),
        style.variables.get("--surface", theme.surface),
    ]
    for name in TEXT_VARIABLES:
        value = style.variables.get(name)
        if value is None or not _HEX.fullmatch(value.lower()):
            continue
        for background in backgrounds:
            if not _HEX.fullmatch(background.lower()):
                continue
            ratio = contrast_ratio(value, background)
            if ratio < NORMAL_TEXT_CONTRAST:
                problems.append(
                    f"{name} ({value}) と背景 ({background}) のコントラストが "
                    f"{ratio:.2f}:1 で、通常サイズ文字の "
                    f"{NORMAL_TEXT_CONTRAST}:1 を下回ります。"
                )
    return problems


def audit_reduced_motion(css: str) -> list[str]:
    """「動きを減らす」設定でアニメーションが本当に止まるかを検査する。

    止める側のセレクターが動かす側より弱いと、CSS の詳細度で負けて止まらない。
    実際に `html[data-theme="motion"][data-motion="enabled"] .article` で動かし、
    `html[data-theme="motion"] .article` で止めようとしていたため、
    利用者が「動きを減らす」を選んでもアニメーションが止まらなかった。

    詳細度を自前で計算すると判定を間違えたときに気づけないので、
    「動かしているセレクターと、まったく同じ文字列の停止ルールがあること」
    という単純で確実な条件にしている。同じセレクターなら詳細度は必ず等しく、
    停止側を後に書けば勝つ。
    """
    animated = {
        selector.strip()
        for selector in re.findall(
            r"([^{}]+?)\{[^}]*?animation\s*:\s*(?!none)[^};]+;", _strip_media(css)
        )
    }
    if not animated:
        return []

    stopped = set()
    for block in re.findall(
        r"@media[^{]*prefers-reduced-motion\s*:\s*reduce[^{]*\{(.*?\})\s*\}", css, re.S
    ):
        for selector, body in re.findall(r"([^{}]+)\{([^}]*)\}", block):
            if re.search(r"animation\s*:\s*none", body):
                stopped.add(selector.strip())

    return [
        f"「{selector}」を止める @media (prefers-reduced-motion: reduce) の"
        "ルールが同じセレクターで書かれていません（詳細度で負けて止まりません）。"
        for selector in sorted(animated - stopped)
    ]


def _strip_media(css: str) -> str:
    """@media ブロックを取り除く（停止ルール自身を「動かす側」と誤認しないため）。"""
    return re.sub(r"@media[^{]*\{.*?\}\s*\}", "", css, flags=re.S)
