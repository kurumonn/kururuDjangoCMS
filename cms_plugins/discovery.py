"""デプロイ時に許可されたPythonパッケージだけをDjangoへ組み込む。"""

from __future__ import annotations

import re
from importlib import metadata

ENTRY_POINT_GROUP = "kururucms.plugins"
_DOTTED_PATH = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+$")


def discover_plugin_apps(allowed_names: list[str]) -> list[str]:
    if not allowed_names:
        return []
    if len(allowed_names) != len(set(allowed_names)):
        raise RuntimeError("プラグイン許可リストに重複があります")
    entries = metadata.entry_points().select(group=ENTRY_POINT_GROUP)
    by_name = {}
    for entry in entries:
        if entry.name not in allowed_names:
            continue
        if entry.name in by_name:
            raise RuntimeError(f"プラグインのentry point名が重複しています: {entry.name}")
        by_name[entry.name] = entry

    missing = sorted(set(allowed_names) - set(by_name))
    if missing:
        raise RuntimeError("許可したプラグインがインストールされていません: " + ", ".join(missing))

    apps = []
    for name in allowed_names:
        value = by_name[name].value
        if "[" in value or ":" in value or not _DOTTED_PATH.fullmatch(value):
            raise RuntimeError(f"{name}: entry pointはDjango AppConfigの完全修飾名で指定してください")
        apps.append(value)
    return apps
