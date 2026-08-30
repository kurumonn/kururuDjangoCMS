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

    def test_database_admin_credentials_are_isolated_from_application_services(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        production = (ROOT / "config" / "settings" / "production.py").read_text(
            encoding="utf-8"
        )
        entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(
            encoding="utf-8"
        )
        provision = (ROOT / "docker" / "provision_db_role.sh").read_text(
            encoding="utf-8"
        )

        db = compose.split("  db:", 1)[1].split("\n  db_role_provision:", 1)[0]
        role_job = compose.split("  db_role_provision:", 1)[1].split(
            "\n  redis:", 1
        )[0]
        web = compose.split("  web:", 1)[1].split(
            "\n  contact_forms_worker:", 1
        )[0]
        worker = compose.split("  contact_forms_worker:", 1)[1].split(
            "\n  contact_forms_maintenance:", 1
        )[0]
        migrate = compose.split("  migrate:", 1)[1].split("\n  web:", 1)[0]

        self.assertIn(".env.db-admin", db)
        self.assertIn(".env.db-admin", role_job)
        self.assertNotIn(".env.db-admin", web)
        self.assertNotIn(".env.db-admin", worker)
        self.assertIn(".env.db-migration", role_job)
        self.assertIn(".env.db-migration", migrate)
        self.assertNotIn(".env.db-migration", web)
        self.assertNotIn(".env.db-migration", worker)
        self.assertIn('"POSTGRES_APP"', production)
        self.assertIn("POSTGRES_MIGRATION", production)
        self.assertNotIn('require("POSTGRES_USER")', production)
        self.assertIn('"POSTGRES_APP"', entrypoint)
        self.assertIn("POSTGRES_MIGRATION", entrypoint)
        self.assertIn("NOSUPERUSER", provision)
        self.assertIn("NOCREATEDB", provision)
        self.assertIn("NOCREATEROLE", provision)
        self.assertIn("NOBYPASSRLS", provision)
        self.assertIn("REVOKE CREATE ON SCHEMA public FROM PUBLIC", provision)
        self.assertIn("GRANT SELECT, INSERT, UPDATE, DELETE", provision)
        self.assertIn("ALTER DEFAULT PRIVILEGES", provision)
        role_test = (ROOT / "scripts" / "verify_db_role_boundary.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("CREATE TABLE role_boundary_probe", role_test)
        self.assertIn("INSERT INTO role_boundary_probe", role_test)
        self.assertIn("application role unexpectedly created a table", role_test)
        self.assertIn("condition: service_completed_successfully", role_job + compose)

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
        self.assertIn("- mail_egress", worker)
        self.assertIn('profiles: ["contact-forms"]', maintenance)
        self.assertIn("run_contact_forms_maintenance", maintenance)
        self.assertIn('"86400"', maintenance)
        self.assertIn("check_contact_forms_health", maintenance)
        self.assertIn("web|worker)", entrypoint)

        web = compose.split("  web:", 1)[1].split(
            "\n  contact_forms_worker:", 1
        )[0]
        self.assertNotIn("- mail_egress", web)
        self.assertNotIn("- mail_egress", maintenance)
        self.assertIn("mail_egress:", compose.split("\nnetworks:", 1)[1])

        host_check = (ROOT / "scripts" / "check_contact_forms_runtime.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("contact_forms_worker contact_forms_maintenance", host_check)
        self.assertIn(".State.Running", host_check)
        self.assertIn(".State.Health.Status", host_check)
        self.assertIn("check_contact_forms_health", host_check)

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
