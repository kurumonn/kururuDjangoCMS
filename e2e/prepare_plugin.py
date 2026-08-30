"""Install one prebuilt plugin wheel into the CMS image build context."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "e2e" / "runtime"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True, type=Path)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        raise SystemExit(f"wheel not found: {wheel}")
    match = re.fullmatch(
        r"kururucms_contact_forms-([0-9][0-9A-Za-z.]*)-py3-none-any[.]whl",
        wheel.name,
    )
    if not match:
        raise SystemExit(f"unexpected wheel name: {wheel.name}")

    lock = ROOT / "plugin-requirements.lock"
    original = lock.read_text(encoding="utf-8")
    if original.strip():
        raise SystemExit("E2E refuses to replace a non-empty plugin-requirements.lock")
    wheel_dir = ROOT / "plugin_wheels"
    unexpected = [path.name for path in wheel_dir.iterdir() if path.name != ".gitkeep"]
    if unexpected:
        raise SystemExit("E2E refuses existing plugin wheels: " + ", ".join(unexpected))

    destination = wheel_dir / wheel.name
    shutil.copy2(wheel, destination)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    lock.write_text(
        f"kururucms-contact-forms=={match.group(1)} --hash=sha256:{digest}\n",
        encoding="utf-8",
    )
    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "plugin-manifest.json").write_text(
        json.dumps(
            {
                "wheel": str(destination.relative_to(ROOT)),
                "lock": str(lock.relative_to(ROOT)),
                "original_lock": original,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"e2e_plugin=prepared sha256={digest}")


if __name__ == "__main__":
    main()
