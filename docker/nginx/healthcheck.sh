#!/bin/sh
set -eu

body="$({
    wget -qO- \
        --no-check-certificate \
        --header="Host: ${NGINX_SERVER_NAME}" \
        https://127.0.0.1/healthz/
} 2>/dev/null)"

case "$body" in
    *'"status": "ok"'*) exit 0 ;;
    *) exit 1 ;;
esac
