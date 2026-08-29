"""バックアップ運用の安全条件をリポジトリ上で固定する。"""

from pathlib import Path
import subprocess
import sys
import tempfile

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class BackupScriptSecurityTests(SimpleTestCase):
    def test_backup_is_private_encrypted_and_never_writes_plaintext_dump(self):
        script = (ROOT / "scripts" / "backup.sh").read_text(encoding="utf-8")

        self.assertIn("umask 077", script)
        self.assertIn('chmod 700 "$BACKUP_DIR"', script)
        self.assertIn(".dump.enc", script)
        self.assertIn("openssl enc -aes-256-cbc", script)
        self.assertNotIn('DB_DUMP="$BACKUP_DIR/db-$STAMP.dump"', script)
        self.assertIn("hmac_file.py", script)
        self.assertIn(".hmac", script)

    def test_restore_accepts_only_encrypted_dump_and_streams_decryption(self):
        script = (ROOT / "scripts" / "restore_drill.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("*.dump.enc", script)
        self.assertIn("openssl enc -d -aes-256-cbc", script)
        self.assertIn("| docker compose exec -T db", script)
        self.assertIn("verify_hmac", script)
        self.assertIn("*.tar.gz.enc", script)
        self.assertIn("tar -xzf -", script)

    def test_restore_drill_requires_database_and_media_pair(self):
        script = (ROOT / "scripts" / "restore_drill.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("<暗号化DB> <暗号化media>", script)
        self.assertIn('MEDIA="${2:', script)
        self.assertNotIn("現在の本番と比べます", script)

    def test_example_configuration_contains_only_a_key_path(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn(
            "BACKUP_ENCRYPTION_KEY_FILE=/etc/kururucms/backup-passphrase",
            example,
        )

    def test_linux_smoke_covers_restore_and_tamper_rejection(self):
        script = (ROOT / "scripts" / "restore_smoke_linux.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("./scripts/backup.sh", script)
        self.assertIn("./scripts/restore_drill.sh", script)
        self.assertIn("tampered.dump.enc", script)
        self.assertIn("conv=notrunc", script)

    def test_hmac_helper_reads_secret_from_file_not_process_arguments(self):
        helper = (ROOT / "scripts" / "hmac_file.py").read_text(encoding="utf-8")

        self.assertIn("hmac.new", helper)
        self.assertIn("Path(args.key_file).read_bytes()", helper)
        self.assertNotIn("hexkey:", helper)

    def test_hmac_helper_detects_payload_changes(self):
        helper = ROOT / "scripts" / "hmac_file.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            key = tmp / "backup-passphrase"
            payload = tmp / "backup.dump.enc"
            key.write_bytes(bytes(range(32)))
            payload.write_bytes(b"original encrypted payload")

            original = subprocess.run(
                [sys.executable, str(helper), str(key), str(payload)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            payload.write_bytes(b"tampered encrypted payload")
            tampered = subprocess.run(
                [sys.executable, str(helper), str(key), str(payload)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()

        self.assertNotEqual(original, tampered)
