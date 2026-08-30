"""Remove only artifacts recorded by the E2E preparation scripts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "e2e" / "runtime"


def safe_path(relative: str) -> Path:
    candidate = (ROOT / relative).resolve()
    if candidate != ROOT and ROOT not in candidate.parents:
        raise RuntimeError(f"refusing path outside repository: {candidate}")
    return candidate


def main() -> None:
    environment_manifest = RUNTIME / "manifest.json"
    if environment_manifest.exists():
        data = json.loads(environment_manifest.read_text(encoding="utf-8"))
        for relative in data.get("created", []):
            path = safe_path(relative)
            if path.is_file() or path.is_symlink():
                path.unlink()
        environment_manifest.unlink()

    plugin_manifest = RUNTIME / "plugin-manifest.json"
    if plugin_manifest.exists():
        data = json.loads(plugin_manifest.read_text(encoding="utf-8"))
        wheel = safe_path(data["wheel"])
        if wheel.is_file() or wheel.is_symlink():
            wheel.unlink()
        safe_path(data["lock"]).write_text(data["original_lock"], encoding="utf-8")
        plugin_manifest.unlink()

    for directory in (RUNTIME,):
        try:
            directory.rmdir()
        except OSError:
            pass
    print("e2e_cleanup=ok")


if __name__ == "__main__":
    main()
