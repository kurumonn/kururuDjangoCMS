"""デプロイ時に許可されたPythonパッケージだけをDjangoへ組み込む。"""

from __future__ import annotations

import re
from importlib import metadata

from django.apps import AppConfig

ENTRY_POINT_GROUP = "kururucms.plugins"
_DOTTED_MODULE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")
_ATTRIBUTE = re.compile(r"^[A-Za-z_]\w*$")


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
        entry = by_name[name]
        if (
            entry.extras
            or not entry.attr
            or not _DOTTED_MODULE.fullmatch(entry.module)
            or not _ATTRIBUTE.fullmatch(entry.attr)
        ):
            raise RuntimeError(
                f"{name}: entry pointは module:AppConfigClass 形式で指定してください"
            )
        try:
            app_config = entry.load()
        except (AttributeError, ImportError, ModuleNotFoundError) as exc:
            raise RuntimeError(f"{name}: AppConfigを読み込めません") from exc
        if not isinstance(app_config, type) or not issubclass(app_config, AppConfig):
            raise RuntimeError(f"{name}: entry pointはDjango AppConfigクラスを指してください")
        apps.append(f"{entry.module}.{entry.attr}")
    return apps
