"""コードで許可したテーマだけを静的ファイルから選択する。"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError


@dataclass(frozen=True)
class ThemeDefinition:
    key: str
    label: str
    css_path: str


THEMES = (
    ThemeDefinition("clean", "Clean", "themes/clean.css"),
    ThemeDefinition("seo-focus", "軽量・可読性", "themes/seo-focus.css"),
    ThemeDefinition("midnight", "Midnight", "themes/midnight.css"),
    ThemeDefinition("cyber-neon", "Cyber Neon", "themes/cyber-neon.css"),
    ThemeDefinition("sakura", "Sakura", "themes/sakura.css"),
    ThemeDefinition("mint-candy", "Mint Candy", "themes/mint-candy.css"),
    ThemeDefinition("glass", "Glass", "themes/glass.css"),
    ThemeDefinition("editorial", "Editorial", "themes/editorial.css"),
    ThemeDefinition("terminal", "Terminal", "themes/terminal.css"),
    ThemeDefinition("motion", "Motion", "themes/motion.css"),
    ThemeDefinition("high-contrast", "High Contrast", "themes/high-contrast.css"),
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
