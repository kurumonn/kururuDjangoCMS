"""Run the production-shaped Docker Compose E2E sequence cross-platform."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--project-name",
    "kururucms-e2e",
    "-f",
    "compose.yaml",
    "-f",
    "e2e/compose.yaml",
]


def run(*args: str, check: bool = True, capture: bool = False):
    return subprocess.run(
        [*COMPOSE, *args],
        cwd=ROOT,
        check=check,
        text=True,
        capture_output=capture,
    )


def django_script(path: str, *, phase: str | None = None, capture: bool = False):
    args = ["exec", "-T"]
    if phase is not None:
        args.extend(["-e", f"E2E_ASSERT_PHASE={phase}"])
    args.extend(
        [
            "web",
            "python",
            "manage.py",
            "shell",
            "-c",
            f"exec(compile(open('{path}', encoding='utf-8').read(), '{path}', 'exec'))",
        ]
    )
    return run(*args, capture=capture, check=not capture)


def wait_for_state(phase: str, timeout: int = 30) -> None:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = django_script("/e2e/assert_state.py", phase=phase, capture=True)
        if last.returncode == 0:
            print(last.stdout.strip())
            return
        time.sleep(1)
    if last is not None:
        sys.stderr.write(last.stdout)
        sys.stderr.write(last.stderr)
    raise RuntimeError(f"E2E state did not converge: {phase}")


def main() -> int:
    required = [
        ROOT / ".env",
        ROOT / ".env.db-admin",
        ROOT / ".env.db-migration",
        ROOT / "e2e" / "runtime" / "manifest.json",
        ROOT / "e2e" / "runtime" / "plugin-manifest.json",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("prepare E2E inputs first: " + ", ".join(missing))

    failed = False
    phase = "startup"
    try:
        run("up", "-d", "--build", "smtp_capture", "nginx")
        phase = "seed"
        django_script("/e2e/seed.py")
        phase = "browser-enqueue"
        run(
            "run",
            "--rm",
            "playwright",
            "--grep",
            "@authorization|@enqueue",
        )
        phase = "database-enqueued"
        wait_for_state("enqueued")

        phase = "worker-start"
        run("up", "-d", "contact_forms_worker", "contact_forms_maintenance")
        phase = "browser-delivery"
        run("run", "--rm", "playwright", "--grep", "@delivery")
        phase = "database-delivered"
        wait_for_state("delivered")
        phase = "maintenance"
        wait_for_state("maintenance")
        print("docker_e2e=passed tests=3")
        return 0
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        failed = True
        return_code = getattr(exc, "returncode", "n/a")
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(
                f"::error title=Docker E2E failed::"
                f"phase={phase}; error={type(exc).__name__}; returncode={return_code}"
            )
        raise
    finally:
        if failed:
            run("ps", check=False)
            run("logs", "--no-color", "--tail", "200", check=False)
        run("down", "--volumes", "--remove-orphans", check=False)
        subprocess.run(
            [sys.executable, os.fspath(ROOT / "e2e" / "cleanup.py")],
            cwd=ROOT,
            check=False,
        )


if __name__ == "__main__":
    raise SystemExit(main())
