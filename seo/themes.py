"""コードで許可したテーマだけを静的ファイルから選択する。

テーマの本体は CSS だが、配色のうち **明暗（scheme）と背景色だけ** は
Python 側にも持たせている。理由は2つある。

1. アクセント色は管理画面から変えられる。そのアクセント色が
   「いま選ばれているテーマの背景」の上で読めるかどうかは、
   テーマの背景色を知らないと判定できない。
2. 利用者のOS設定（prefers-color-scheme）でアクセント色を切り替えると、
   背景はテーマ固定・リンク色はOS依存という食い違いが起きる。
   実際にこの食い違いでリンクのコントラストが 2.3:1 まで落ちていた。

ここに書いた値と CSS の実際の宣言がずれると意味がないので、
`seo.checks` のシステムチェックが両者の一致を毎回検証する。
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError

LIGHT = "light"
DARK = "dark"


@dataclass(frozen=True)
class ThemeDefinition:
    key: str
    label: str
    css_path: str
    # CSS の color-scheme と同じ値。アクセント色の明/暗を選ぶのに使う。
    scheme: str
    # CSS の --bg / --surface と同じ値。リンク色の判定に使う。
    background: str
    surface: str

    @property
    def backgrounds(self) -> tuple[str, str]:
        """文字色を載せる背景の一覧（ページ地とカード地）。"""
        return (self.background, self.surface)


THEMES = (
    ThemeDefinition("clean", "Clean", "themes/clean.css", LIGHT, "#f7f8fb", "#ffffff"),
    ThemeDefinition(
        "seo-focus", "軽量・可読性", "themes/seo-focus.css", LIGHT, "#ffffff", "#ffffff"
    ),
    ThemeDefinition(
        "midnight", "Midnight", "themes/midnight.css", DARK, "#0b1020", "#141b2d"
    ),
    ThemeDefinition(
        "cyber-neon", "Cyber Neon", "themes/cyber-neon.css", DARK, "#070b12", "#101827"
    ),
    ThemeDefinition(
        "sakura", "Sakura", "themes/sakura.css", LIGHT, "#fff7fa", "#ffffff"
    ),
    ThemeDefinition(
        "mint-candy", "Mint Candy", "themes/mint-candy.css", LIGHT, "#f0fff9", "#ffffff"
    ),
    ThemeDefinition("glass", "Glass", "themes/glass.css", LIGHT, "#eaf2ff", "#fbfdff"),
    ThemeDefinition(
        "editorial", "Editorial", "themes/editorial.css", LIGHT, "#f5f1e8", "#fffdf8"
    ),
    ThemeDefinition(
        "terminal", "Terminal", "themes/terminal.css", DARK, "#07110b", "#0d1b12"
    ),
    ThemeDefinition(
        "motion", "Motion", "themes/motion.css", LIGHT, "#f7f4ff", "#ffffff"
    ),
    ThemeDefinition(
        "high-contrast",
        "High Contrast",
        "themes/high-contrast.css",
        LIGHT,
        "#ffffff",
        "#ffffff",
    ),
)
THEME_BY_KEY = {theme.key: theme for theme in THEMES}
DEFAULT_THEME_KEY = "clean"


def theme_choices():
    return [(theme.key, theme.label) for theme in THEMES]


def resolve_theme(key: str) -> ThemeDefinition:
    return THEME_BY_KEY.get(key, THEME_BY_KEY[DEFAULT_THEME_KEY])


def validate_theme_key(value: str) -> None:
    if value not in THEME_BY_KEY:
        raise ValidationError("許可されていないテーマです。")
