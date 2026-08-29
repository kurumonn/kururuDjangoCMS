"""公開用 Compose 経路が安全側に倒れることを固定する。"""

from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class ProductionRuntimeContractTests(SimpleTestCase):
    def test_nginx_terminates_tls_and_plain_http_only_redirects(self):
        template = (
            ROOT / "docker" / "nginx" / "default.conf.template"
        ).read_text(encoding="utf-8")

        self.assertIn("listen 443 ssl", template)
        self.assertIn("ssl_certificate /run/secrets/tls_certificate", template)
        self.assertIn("ssl_certificate_key /run/secrets/tls_private_key", template)
        self.assertIn("ssl_protocols TLSv1.2 TLSv1.3", template)
        self.assertIn("return 308 https://$server_name$request_uri", template)
        self.assertNotIn("proxy_pass http://django", template.split("listen 80", 1)[1].split("listen 443", 1)[0])

    def test_compose_requires_tls_material_and_probes_public_https_path(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn('"443:443"', compose)
        self.assertIn("tls_certificate:", compose)
        self.assertIn("tls_private_key:", compose)
        self.assertIn("TLS_CERTIFICATE_FILE", compose)
        self.assertIn("TLS_PRIVATE_KEY_FILE", compose)
        healthcheck = (
            ROOT / "docker" / "nginx" / "healthcheck.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("https://127.0.0.1/healthz/", healthcheck)

    def test_schema_and_static_release_is_a_one_shot_job(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

        self.assertIn("migrate:", compose)
        self.assertIn("condition: service_completed_successfully", compose)
        self.assertIn("condition: service_healthy", compose)
        self.assertIn("APP_START_MODE", entrypoint)
        self.assertIn("python manage.py migrate --check", entrypoint)
        self.assertIn('APP_START_MODE: "migrate"', compose)

    def test_contact_forms_background_services_are_opt_in_and_monitored(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(
            encoding="utf-8"
        )

        worker = compose.split("  contact_forms_worker:", 1)[1].split(
            "\n  contact_forms_maintenance:", 1
        )[0]
        maintenance = compose.split("  contact_forms_maintenance:", 1)[1].split(
            "\n  nginx:", 1
        )[0]

        self.assertIn('profiles: ["contact-forms"]', worker)
        self.assertIn("process_contact_mail_outbox", worker)
        self.assertIn("check_contact_forms_health", worker)
        self.assertIn('APP_START_MODE: "worker"', worker)
        self.assertIn('profiles: ["contact-forms"]', maintenance)
        self.assertIn("run_contact_forms_maintenance", maintenance)
        self.assertIn('"86400"', maintenance)
        self.assertIn("check_contact_forms_health", maintenance)
        self.assertIn("web|worker)", entrypoint)

    def test_nginx_follows_explicit_web_restarts(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        nginx_dependency = compose.split("  nginx:", 1)[1].split(
            "    environment:", 1
        )[0]

        self.assertIn("condition: service_healthy", nginx_dependency)
        self.assertIn("restart: true", nginx_dependency)

    def test_frontend_cannot_reach_database_or_redis_network(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")

        self.assertIn("frontend:", compose)
        self.assertIn("backend:", compose)
        self.assertIn("internal: true", compose)
        nginx_service = compose.split("  nginx:", 1)[1].split("\nsecrets:", 1)[0]
        self.assertIn("- frontend", nginx_service)
        self.assertNotIn("- backend", nginx_service)

    def test_redis_requires_authentication(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        redis_service = compose.split("  redis:", 1)[1].split("\n  migrate:", 1)[0]

        self.assertIn("REDIS_PASSWORD", redis_service)
        self.assertIn("--requirepass", redis_service)
        self.assertIn("REDISCLI_AUTH", redis_service)
