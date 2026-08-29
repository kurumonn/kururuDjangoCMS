"""再現可能な依存・コンテナ固定の回帰テスト。"""

import re
from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


class SupplyChainPinningTests(SimpleTestCase):
    def test_ci_uses_node24_actions_and_hash_locked_dependencies(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        ci_lock = (ROOT / "requirements-ci.lock").read_text(encoding="utf-8")

        self.assertIn(
            "actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09",
            workflow,
        )
        self.assertIn(
            "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
            workflow,
        )
        self.assertIn("requirements-ci.lock", workflow)
        self.assertIn("--require-hashes", workflow)
        self.assertNotIn("requirements-ci.txt\n", workflow)
        self.assertIn("pip==25.3", ci_lock)
        self.assertGreater(ci_lock.count("--hash=sha256:"), 20)

    def test_python_build_uses_lock_and_requires_hashes(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

        self.assertEqual(
            dockerfile.count("FROM python:3.12-slim@sha256:"),
            2,
        )
        self.assertIn("COPY requirements.lock .", dockerfile)
        self.assertEqual(dockerfile.count("--require-hashes"), 4)
        self.assertIn("COPY plugin-requirements.lock .", dockerfile)
        self.assertIn("COPY plugin_wheels /plugin-wheels", dockerfile)
        self.assertIn("--no-index --find-links=/plugin-wheels", dockerfile)
        self.assertGreaterEqual(dockerfile.count("--no-deps"), 2)
        self.assertIn("--only-binary=:all:", dockerfile)
        self.assertNotIn("apt-get", dockerfile)

    def test_every_compose_registry_image_has_a_digest(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        image_values = [
            line.split("image:", 1)[1].strip()
            for line in compose.splitlines()
            if line.strip().startswith("image:")
        ]

        self.assertTrue(image_values)
        self.assertTrue(all(DIGEST.search(image) for image in image_values))

    def test_lock_contains_transitive_dependencies_and_hashes(self):
        lock = (ROOT / "requirements.lock").read_text(encoding="utf-8")

        self.assertIn("django-allauth[mfa,socialaccount]==65.18.0", lock)
        self.assertIn("requests==", lock)
        self.assertIn("cryptography==", lock)
        self.assertGreater(lock.count("--hash=sha256:"), 20)

    def test_docker_context_excludes_backups_and_local_keys(self):
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

        for pattern in ("backups/", ".local-backup-key", ".local-certs/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, ignore)
