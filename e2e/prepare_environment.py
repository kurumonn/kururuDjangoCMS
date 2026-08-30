"""Create isolated, random E2E credentials without overwriting developer files."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "e2e" / "runtime"
ENV_FILES = (ROOT / ".env", ROOT / ".env.db-admin", ROOT / ".env.db-migration")


def write_private(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


def random_value() -> str:
    return secrets.token_urlsafe(48)


def openssl_command() -> str:
    discovered = shutil.which("openssl")
    if discovered:
        return discovered
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles", r"C:\\Program Files"))
        for relative in ("Git/usr/bin/openssl.exe", "Git/mingw64/bin/openssl.exe"):
            candidate = program_files / relative
            if candidate.is_file():
                return os.fspath(candidate)
    raise SystemExit("OpenSSL is required to create the one-day E2E certificate")


def main() -> None:
    existing = [str(path) for path in ENV_FILES if path.exists()]
    if existing:
        raise SystemExit(
            "E2E refuses to overwrite existing environment files: " + ", ".join(existing)
        )
    RUNTIME.mkdir(parents=True, exist_ok=True)
    manifest = RUNTIME / "manifest.json"
    if manifest.exists():
        raise SystemExit("E2E runtime manifest already exists; run e2e/cleanup.py first")

    app_password = random_value()
    redis_password = random_value()
    viewer_password = random_value()
    write_private(
        ROOT / ".env",
        [
            f"DJANGO_SECRET_KEY={random_value()}",
            f"DJANGO_COMMENT_IP_HASH_KEY={random_value()}",
            "DJANGO_ALLOWED_HOSTS=e2e.local",
            "DJANGO_ADMIN_URL_PATH=admin",
            "DJANGO_TRUSTED_PROXY_COUNT=1",
            "DJANGO_MFA_REQUIRED_FOR_STAFF=0",
            "DJANGO_SECURE_HSTS_SECONDS=3600",
            "DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=0",
            "DJANGO_SECURE_HSTS_PRELOAD=0",
            "KURURU_PLUGIN_PACKAGES=contact_forms",
            f"KURURU_FORMS_IP_HASH_KEY={random_value()}",
            "POSTGRES_DB=kururucms_e2e",
            "POSTGRES_APP_USER=kururucms_app",
            f"POSTGRES_APP_PASSWORD={app_password}",
            "POSTGRES_HOST=db",
            f"REDIS_PASSWORD={redis_password}",
            "REDIS_HOST=redis",
            "DJANGO_EMAIL_HOST=smtp_capture",
            "DJANGO_EMAIL_PORT=1025",
            "DJANGO_EMAIL_USE_TLS=0",
            "DJANGO_DEFAULT_FROM_EMAIL=noreply@e2e.local",
            "NGINX_SERVER_NAME=e2e.local",
            "TLS_CERTIFICATE_FILE=e2e/runtime/tls.crt",
            "TLS_PRIVATE_KEY_FILE=e2e/runtime/tls.key",
            f"KURURU_E2E_VIEWER_PASSWORD={viewer_password}",
        ],
    )
    write_private(
        ROOT / ".env.db-admin",
        ["POSTGRES_USER=postgres", f"POSTGRES_PASSWORD={random_value()}"],
    )
    write_private(
        ROOT / ".env.db-migration",
        [
            "POSTGRES_MIGRATION_USER=kururucms_migrator",
            f"POSTGRES_MIGRATION_PASSWORD={random_value()}",
        ],
    )

    certificate = RUNTIME / "tls.crt"
    private_key = RUNTIME / "tls.key"
    manifest.write_text(
        json.dumps(
            {
                "created": [
                    os.fspath(path.relative_to(ROOT))
                    for path in (*ENV_FILES, certificate, private_key)
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    subprocess.run(
        [
            openssl_command(),
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            os.fspath(private_key),
            "-out",
            os.fspath(certificate),
            "-days",
            "1",
            "-subj",
            "/CN=e2e.local",
            "-addext",
            "subjectAltName=DNS:e2e.local",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("e2e_environment=prepared")


if __name__ == "__main__":
    main()
