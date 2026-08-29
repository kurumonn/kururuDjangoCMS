#!/usr/bin/env bash
# データベースとアップロード画像を、暗号化してバックアップする。
#
#   ./scripts/backup.sh
#   ./scripts/backup.sh /mnt/backup
#
# 前提: compose の db / web コンテナが動いていること。
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

BACKUP_DIR="${1:-./backups}"
STAMP="$(date +%Y%m%d-%H%M%S)"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# .env には鍵そのものではなく、ホスト上の鍵ファイルのパスだけを書く。
set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${BACKUP_ENCRYPTION_KEY_FILE:?BACKUP_ENCRYPTION_KEY_FILE を .env に設定してください}"
if [ ! -f "$BACKUP_ENCRYPTION_KEY_FILE" ]; then
    echo "[backup] 暗号化鍵ファイルが見つかりません" >&2
    exit 1
fi
KEY_MODE="$(stat -c '%a' "$BACKUP_ENCRYPTION_KEY_FILE")"
if [ "$KEY_MODE" != "400" ] && [ "$KEY_MODE" != "600" ]; then
    echo "[backup] 暗号化鍵ファイルの権限は 0400 または 0600 にしてください" >&2
    exit 1
fi
command -v openssl > /dev/null || {
    echo "[backup] openssl が必要です" >&2
    exit 1
}
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" > /dev/null || {
    echo "[backup] HMAC検証にPython 3が必要です" >&2
    exit 1
}

DB_DUMP="$BACKUP_DIR/db-$STAMP.dump.enc"
MEDIA_TAR="$BACKUP_DIR/media-$STAMP.tar.gz.enc"
DB_HMAC="$DB_DUMP.hmac"
MEDIA_HMAC="$MEDIA_TAR.hmac"
DB_TEMP="$(mktemp "$BACKUP_DIR/.db-$STAMP.XXXXXX")"
MEDIA_TEMP="$(mktemp "$BACKUP_DIR/.media-$STAMP.XXXXXX")"
DB_HMAC_TEMP="$(mktemp "$BACKUP_DIR/.db-hmac-$STAMP.XXXXXX")"
MEDIA_HMAC_TEMP="$(mktemp "$BACKUP_DIR/.media-hmac-$STAMP.XXXXXX")"

cleanup() {
    rm -f -- "$DB_TEMP" "$MEDIA_TEMP" "$DB_HMAC_TEMP" "$MEDIA_HMAC_TEMP"
}
trap cleanup EXIT

encrypt() {
    openssl enc -aes-256-cbc -salt -pbkdf2 -iter 600000 \
        -pass "file:$BACKUP_ENCRYPTION_KEY_FILE"
}

decrypt() {
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
        -pass "file:$BACKUP_ENCRYPTION_KEY_FILE"
}

hmac_file() {
    "$PYTHON_BIN" "$SCRIPT_DIR/hmac_file.py" \
        "$BACKUP_ENCRYPTION_KEY_FILE" "$1"
}

echo "[backup] データベースを書き出して暗号化します..."
docker compose exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
    | encrypt > "$DB_TEMP"

echo "[backup] アップロード画像を固めて暗号化します..."
docker compose exec -T web sh -c 'cd /app && tar -cz media' \
    | encrypt > "$MEDIA_TEMP"

# CBC暗号は単体では改ざんを検知できないため、復号前に検証するHMACを付ける。
hmac_file "$DB_TEMP" > "$DB_HMAC_TEMP"
hmac_file "$MEDIA_TEMP" > "$MEDIA_HMAC_TEMP"

echo "[backup] 暗号化済みファイルを復号ストリームで検査します..."
if [ ! -s "$DB_TEMP" ]; then
    echo "[backup] 失敗: データベースのバックアップが空です" >&2
    exit 1
fi
decrypt < "$DB_TEMP" | docker compose exec -T db sh -c 'pg_restore --list' > /dev/null

if [ ! -s "$MEDIA_TEMP" ]; then
    echo "[backup] 失敗: 画像のバックアップが空です" >&2
    exit 1
fi
decrypt < "$MEDIA_TEMP" | tar -tzf - > /dev/null

chmod 600 "$DB_TEMP" "$MEDIA_TEMP" "$DB_HMAC_TEMP" "$MEDIA_HMAC_TEMP"
mv "$DB_TEMP" "$DB_DUMP"
mv "$MEDIA_TEMP" "$MEDIA_TAR"
mv "$DB_HMAC_TEMP" "$DB_HMAC"
mv "$MEDIA_HMAC_TEMP" "$MEDIA_HMAC"

echo "[backup] $KEEP_DAYS 日より古い暗号化バックアップを削除します..."
find "$BACKUP_DIR" -name 'db-*.dump.enc' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name 'media-*.tar.gz.enc' -mtime "+$KEEP_DAYS" -delete
find "$BACKUP_DIR" -name '*.enc.hmac' -mtime "+$KEEP_DAYS" -delete

echo "[backup] 完了:"
ls -lh "$DB_DUMP" "$DB_HMAC" "$MEDIA_TAR" "$MEDIA_HMAC"
