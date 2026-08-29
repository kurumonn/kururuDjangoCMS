"""Fail CI when detect-secrets finds candidates, without printing values."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True
    ).stdout.split(b"\0")
    tracked_files = [os.fsdecode(path) for path in tracked if path]
    completed = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", *tracked_files],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        print("detect-secrets execution failed", file=sys.stderr)
        return completed.returncode
    findings = json.loads(completed.stdout).get("results", {})
    if findings:
        for path, candidates in sorted(findings.items()):
            message = f"Potential secrets found: {len(candidates)} candidate(s)"
            print(f"Potential secrets found in {path}: {len(candidates)}")
            if os.environ.get("GITHUB_ACTIONS") == "true":
                safe_path = path.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
                print(f"::error file={safe_path}::{message}")
        return 1
    print("detect-secrets: no candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
