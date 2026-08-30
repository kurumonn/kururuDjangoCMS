"""Run the production-shaped Docker Compose E2E sequence cross-platform."""

from __future__ import annotations

import os
import re
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


def diagnostic_code(completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or "") + "\n" + (completed.stdout or "")
    lowered = output.lower()
    if "no matching manifest" in lowered:
        return "image-platform"
    if "failed to solve" in lowered or "dockerfile parse error" in lowered:
        return "image-build"
    if "required variable" in lowered or "is not set" in lowered:
        return "compose-environment"
    if "no space left on device" in lowered:
        return "runner-disk-space"
    if "permission denied" in lowered:
        return "runner-permission"
    for service in (
        "db_role_provision",
        "smtp_capture",
        "migrate",
        "redis",
        "web",
        "nginx",
        "db",
    ):
        if service in lowered:
            return f"service-{service}"
    return "unclassified"


def playwright_diagnostic(completed: subprocess.CompletedProcess[str]) -> str:
    output = (completed.stderr or "") + "\n" + (completed.stdout or "")
    for checkpoint in (
        "navigate-admin",
        "wait-login",
        "submit-login",
        "actions-hidden",
        "forged-post",
        "form-still-visible",
    ):
        if f"checkpoint={checkpoint}" in output:
            return f"checkpoint-{checkpoint}"
    line_match = re.search(r"contact-forms\.spec\.ts:(\d+)", output)
    if line_match:
        line_number = int(line_match.group(1))
        for upper_bound, checkpoint in (
            (11, "navigate-admin"),
            (19, "wait-login"),
            (22, "submit-login"),
            (27, "actions-hidden"),
            (49, "forged-post"),
            (60, "form-still-visible"),
        ):
            if line_number <= upper_bound:
                return f"checkpoint-{checkpoint}"
    lowered = output.lower()
    if "strict mode violation" in lowered:
        return "selector-strict-mode"
    if "timeout" in lowered:
        return "playwright-timeout"
    if "net::err_" in lowered:
        return "browser-network"
    return "playwright-unclassified"


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
    diagnostic = "not-collected"
    try:
        startup = run(
            "up",
            "-d",
            "--build",
            "smtp_capture",
            "nginx",
            check=False,
            capture=True,
        )
        if startup.returncode:
            diagnostic = diagnostic_code(startup)
            raise subprocess.CalledProcessError(startup.returncode, startup.args)
        sys.stdout.write(startup.stdout)
        sys.stderr.write(startup.stderr)
        phase = "seed"
        django_script("/e2e/seed.py")
        phase = "browser-authorization"
        authorization = run(
            "run",
            "--rm",
            "playwright",
            "--reporter=json",
            "--grep",
            "@authorization",
            check=False,
            capture=True,
        )
        if authorization.returncode:
            diagnostic = playwright_diagnostic(authorization)
            raise subprocess.CalledProcessError(
                authorization.returncode, authorization.args
            )
        print("playwright_phase=authorization passed=1")
        phase = "browser-enqueue"
        enqueue = run(
            "run",
            "--rm",
            "playwright",
            "--reporter=json",
            "--grep",
            "@enqueue",
            check=False,
            capture=True,
        )
        if enqueue.returncode:
            diagnostic = playwright_diagnostic(enqueue)
            raise subprocess.CalledProcessError(enqueue.returncode, enqueue.args)
        print("playwright_phase=enqueue passed=1")
        phase = "database-enqueued"
        wait_for_state("enqueued")

        phase = "worker-start"
        run("up", "-d", "contact_forms_worker", "contact_forms_maintenance")
        phase = "browser-delivery"
        delivery = run(
            "run",
            "--rm",
            "playwright",
            "--reporter=json",
            "--grep",
            "@delivery",
            check=False,
            capture=True,
        )
        if delivery.returncode:
            diagnostic = playwright_diagnostic(delivery)
            raise subprocess.CalledProcessError(delivery.returncode, delivery.args)
        print("playwright_phase=delivery passed=1")
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
                f"phase={phase}; error={type(exc).__name__}; returncode={return_code}; "
                f"diagnostic={diagnostic}"
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
