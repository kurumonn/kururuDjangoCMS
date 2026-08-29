#!/bin/sh
# ホスト監視から呼び出す、問い合わせフォーム常駐処理の外形監視。
set -eu

profile="contact-forms"
services="contact_forms_worker contact_forms_maintenance"

for service in $services; do
    container_id="$(docker compose --profile "$profile" ps -q "$service")"
    if [ -z "$container_id" ]; then
        echo "[contact-forms-runtime] $service container is missing" >&2
        exit 1
    fi
    running="$(docker inspect --format '{{.State.Running}}' "$container_id")"
    health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}missing{{end}}' "$container_id")"
    if [ "$running" != "true" ] || [ "$health" != "healthy" ]; then
        echo "[contact-forms-runtime] $service running=$running health=$health" >&2
        exit 1
    fi
done

docker compose --profile "$profile" exec -T contact_forms_worker \
    python manage.py check_contact_forms_health
echo "[contact-forms-runtime] ok"
