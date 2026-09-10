"""色のコントラスト計算。

用途が2つあり、混同すると事故になるので分けてある。

* `readable_foreground()` … **アクセント色を背景に敷いたとき**（ボタンなど）に
  載せる文字を黒と白のどちらにするか。
* `ensure_readable_text()` … **ページやカードの背景の上に置く文字**（リンクなど）が
  読める明るさかどうか。読めないなら同じ色相のまま明るさだけ寄せる。

前者だけを検査していたため、「ボタンの文字は読めるがリンクは読めない」
という抜けが実際に起きた。判定する背景が違う、別々の検査だと考えること。
"""

from functools import lru_cache

# 通常サイズ（16px 前後）の本文に対する WCAG 2.2 の下限。
# 大きな文字には 3:1 の例外があるが、本文とリンクには適用しない。
NORMAL_TEXT_CONTRAST = 4.5


def _rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(char * 2 for char in value)
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _hex(channels: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % channels


def _luminance(hex_color: str) -> float:
    channels = []
    for channel in _rgb(hex_color):
        normalized = channel / 255
        channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def readable_foreground(background: str) -> str:
    """その背景色を敷いたときに載せる文字色（黒か白）を選ぶ。"""
    candidates = ("#000000", "#ffffff")
    return max(candidates, key=lambda color: contrast_ratio(background, color))


def _blend(color: str, endpoint: str, amount: float) -> str:
    source = _rgb(color)
    target = _rgb(endpoint)
    return _hex(
        tuple(
            round(source[index] + (target[index] - source[index]) * amount)
            for index in range(3)
        )
    )


def _worst_ratio(color: str, backgrounds: tuple[str, ...]) -> float:
    return min(contrast_ratio(color, background) for background in backgrounds)


@lru_cache(maxsize=256)
def ensure_readable_text(
    color: str,
    backgrounds: tuple[str, ...],
    target: float = NORMAL_TEXT_CONTRAST,
) -> str:
    """背景の上で読める明るさになるまで、色を白か黒へ寄せる。

    管理画面で選べるアクセント色は自由なので、テーマの背景と組み合わせると
    読めない場合がある（例: 水色 #60a5fa を Clean の白地に置くと 2.5:1）。
    色相を変えずに明るさだけを寄せることで、選んだ色の印象を保ちつつ
    通常サイズ文字の基準を満たす。

    白へ寄せても黒へ寄せても届かない背景（中間の灰色など）では、
    より読みやすくなる側の端の色を返す。呼び出し側で基準未達を
    許容しないよう、システムチェックとテストで別途検証している。
    """
    normalized = _blend(color, color, 0.0)
    if _worst_ratio(normalized, backgrounds) >= target:
        return normalized

    STEPS = 200
    solutions: list[tuple[int, str]] = []
    fallbacks: list[tuple[float, str]] = []
    for endpoint in ("#000000", "#ffffff"):
        # 白へ寄せる途中でいったんコントラストが下がる組み合わせがあるため、
        # 二分探索ではなく端から順に見る。1方向あたり200回の単純計算で終わる。
        for step in range(1, STEPS + 1):
            candidate = _blend(normalized, endpoint, step / STEPS)
            ratio = _worst_ratio(candidate, backgrounds)
            if ratio >= target:
                solutions.append((step, candidate))
                break
        else:
            fallbacks.append((_worst_ratio(endpoint, backgrounds), endpoint))

    if solutions:
        # 元の色からの変化が小さい方を選ぶ（色の印象をなるべく残す）。
        return min(solutions)[1]
    return max(fallbacks)[1]
