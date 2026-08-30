# Docker Compose E2E

This suite verifies the critical Kururu Forms journey against the production
Dockerfile and the normal PostgreSQL, Redis, Gunicorn, nginx, Outbox worker, and
maintenance services. Playwright and the SMTP sink are test-only containers.

The suite intentionally covers only boundaries that unit tests cannot prove:

- a view-only staff user cannot see or forge destructive admin actions;
- a public form is submitted through nginx, HTTPS, CSRF, and the block renderer;
- replaying the same signed token creates one submission and one notification;
- no email is received while the Outbox worker is stopped;
- starting the worker sends the notification and then one autoreply;
- the scheduled maintenance service removes an expired submission.

## Local execution

Use a dedicated clean worktree. The preparation step refuses to run if
`.env`, `.env.db-admin`, or `.env.db-migration` already exists, so it cannot
overwrite a developer or production-like configuration.

Build Kururu Forms first, then run:

    python e2e/prepare_plugin.py --wheel C:\\path\\to\\kururucms_contact_forms-0.2.2-py3-none-any.whl
    python e2e/prepare_environment.py
    python e2e/run.py

`run.py` always removes the `kururucms-e2e` Compose project and its named
volumes, then deletes only files recorded in `e2e/runtime/manifest.json`.
If preparation is interrupted before `run.py` starts, recover with:

    python e2e/cleanup.py

The generated certificate and credentials are random, test-only, ignored by
Git, and never printed. The production Compose file is not relaxed for E2E.

The Playwright container follows the upstream trusted-E2E recommendation:
the browser runs as the image's default root user with an init process and
host IPC. It visits only the fixed Compose hostname, has no Docker socket,
has no host bind mount, and is never used for crawling untrusted websites.
Running as `pwuser` would require the upstream Chromium seccomp profile.
