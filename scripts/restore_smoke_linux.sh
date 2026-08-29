#!/usr/bin/env bash
# 読み取り専用でマウントした作業ツリーを、Linux一時領域へ複製して
# backup.sh / restore_drill.sh と改ざん拒否を実行するrelease smoke test。
set -euo pipefail
umask 077

SOURCE_DIR="${1:-/source}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-djangocms-security-fixes}"
export COMPOSE_PROJECT_NAME

WORK_DIR="$(mktemp -d)"
cleanup() {
    rm -rf -- "$WORK_DIR"
}
trap cleanup EXIT

cp -a "$SOURCE_DIR/." "$WORK_DIR/"
cd "$WORK_DIR"

KEY_FILE="$WORK_DIR/backup-smoke.key"
openssl rand -base64 -out "$KEY_FILE" 48
chmod 600 "$KEY_FILE"
sed -i "s|^BACKUP_ENCRYPTION_KEY_FILE=.*|BACKUP_ENCRYPTION_KEY_FILE=$KEY_FILE|" .env

./scripts/backup.sh "$WORK_DIR/backups"
DB_BACKUP="$(find "$WORK_DIR/backups" -name 'db-*.dump.enc' -type f)"
MEDIA_BACKUP="$(find "$WORK_DIR/backups" -name 'media-*.tar.gz.enc' -type f)"
./scripts/restore_drill.sh "$DB_BACKUP" "$MEDIA_BACKUP"

TAMPERED="$WORK_DIR/backups/tampered.dump.enc"
cp "$DB_BACKUP" "$TAMPERED"
cp "$DB_BACKUP.hmac" "$TAMPERED.hmac"
printf X | dd of="$TAMPERED" bs=1 seek=32 count=1 conv=notrunc status=none
if ./scripts/restore_drill.sh "$TAMPERED" "$MEDIA_BACKUP"; then
    echo "[smoke] 改ざん暗号文が拒否されませんでした" >&2
    exit 1
fi

echo "[smoke] DB・media復元と改ざん拒否を確認しました。"
