"""テーマCSSの起動時チェック。

許可リストに載せた CSS が存在するかだけでなく、
「共通CSSが参照する変数を全部定義しているか」「本文が読める配色か」
までここで見る。

理由は実際の事故から。テーマ側が `--fg` ではなく `--text` を定義していたため、
背景だけがテーマの色になり、本文色は共通CSSの初期値が残った。
ファイルは存在していたのでこのチェックは通り、テストも
「data-theme とCSSパスが出力されるか」しか見ていなかったので通り、
ブラウザで見て初めて 1.17:1 という数字が出た。
存在確認だけのチェックは、存在以外の何も保証しない。
"""

from django.contrib.staticfiles import finders
from django.core.checks import Error, register

from .theme_css import audit_reduced_motion, audit_theme
from .themes import THEMES

SITE_CSS_PATH = "css/site.css"


def read_static(path: str) -> str | None:
    found = finders.find(path)
    if found is None:
        return None
    with open(found, encoding="utf-8") as handle:
        return handle.read()


@register()
def theme_static_files_check(app_configs, **kwargs):
    errors = []
    for theme in THEMES:
        css = read_static(theme.css_path)
        if css is None:
            errors.append(
                Error(f"テーマCSSが見つかりません: {theme.css_path}", id="seo.E001")
            )
            continue
        errors.extend(
            Error(f"{theme.css_path}: {problem}", id="seo.E002")
            for problem in audit_theme(theme, css)
        )
        errors.extend(
            Error(f"{theme.css_path}: {problem}", id="seo.E003")
            for problem in audit_reduced_motion(css)
        )
    return errors


@register()
def reduced_motion_killswitch_check(app_configs, **kwargs):
    """共通CSS側に、テーマより強い停止ルールが残っているかを見る。

    テーマごとの停止ルールは書き忘れも詳細度の間違いも起きる。
    最後の砦として、共通CSSに !important 付きの停止ルールを置いている。
    ここが消えると「利用者設定を優先する」と言えなくなる。
    """
    css = read_static(SITE_CSS_PATH)
    if css is None:
        return [Error(f"共通CSSが見つかりません: {SITE_CSS_PATH}", id="seo.E004")]
    if (
        "prefers-reduced-motion" not in css
        or "animation-duration: 0.01ms !important" not in css
    ):
        return [
            Error(
                f"{SITE_CSS_PATH}: @media (prefers-reduced-motion: reduce) の"
                "全体停止ルール（!important 付き）がありません。",
                id="seo.E005",
            )
        ]
    return []
