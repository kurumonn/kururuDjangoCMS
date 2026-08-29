#!/usr/bin/env bash
# 暗号化バックアップを、使い捨ての別DBへ復元する訓練。
# 本番のデータベースには一切書き込まない。
#
#   ./scripts/restore_drill.sh <暗号化DB> <暗号化media>
set -euo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

DUMP="${1:?使い方: ./scripts/restore_drill.sh <暗号化DB> <暗号化media>}"
MEDIA="${2:?使い方: ./scripts/restore_drill.sh <暗号化DB> <暗号化media>}"
DRILL_DB="kururucms_restore_drill"

case "$DUMP" in
    *.dump.enc) ;;
    *)
        echo "[drill] 暗号化された *.dump.enc だけを受け付けます" >&2
        exit 1
        ;;
esac
case "$MEDIA" in
    *.tar.gz.enc) ;;
    *)
        echo "[drill] 暗号化された *.tar.gz.enc だけを受け付けます" >&2
        exit 1
        ;;
esac
for artifact in "$DUMP" "$DUMP.hmac" "$MEDIA" "$MEDIA.hmac"; do
    if [ ! -s "$artifact" ]; then
        echo "[drill] バックアップが見つからないか空です: $artifact" >&2
        exit 1
    fi
done

set -a
# shellcheck disable=SC1091
. ./.env
# 復元訓練だけがDB作成権限を使うため、管理者envを明示的に読む。
# shellcheck disable=SC1091
. ./.env.db-admin
set +a

: "${BACKUP_ENCRYPTION_KEY_FILE:?BACKUP_ENCRYPTION_KEY_FILE を .env に設定してください}"
if [ ! -f "$BACKUP_ENCRYPTION_KEY_FILE" ]; then
    echo "[drill] 暗号化鍵ファイルが見つかりません" >&2
    exit 1
fi
KEY_MODE="$(stat -c '%a' "$BACKUP_ENCRYPTION_KEY_FILE")"
if [ "$KEY_MODE" != "400" ] && [ "$KEY_MODE" != "600" ]; then
    echo "[drill] 暗号化鍵ファイルの権限は 0400 または 0600 にしてください" >&2
    exit 1
fi
command -v openssl > /dev/null || {
    echo "[drill] openssl が必要です" >&2
    exit 1
}
PYTHON_BIN="${PYTHON_BIN:-python3}"
command -v "$PYTHON_BIN" > /dev/null || {
    echo "[drill] HMAC検証にPython 3が必要です" >&2
    exit 1
}

hmac_file() {
    "$PYTHON_BIN" "$SCRIPT_DIR/hmac_file.py" \
        "$BACKUP_ENCRYPTION_KEY_FILE" "$1"
}

verify_hmac() {
    actual="$(hmac_file "$1")"
    expected="$(tr -d '\r\n' < "$2")"
    if [ "${#expected}" -ne 64 ] || [ "$actual" != "$expected" ]; then
        echo "[drill] 改ざんまたは鍵の不一致を検出しました: $1" >&2
        exit 1
    fi
}

decrypt() {
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
        -pass "file:$BACKUP_ENCRYPTION_KEY_FILE"
}

# 復号・DB書き込みより先に暗号文全体を認証する。
verify_hmac "$DUMP" "$DUMP.hmac"
verify_hmac "$MEDIA" "$MEDIA.hmac"

if [ "$DRILL_DB" = "$POSTGRES_DB" ]; then
    echo "[drill] 中止: 訓練用の名前が本番と同じです" >&2
    exit 1
fi

MEDIA_DRILL_DIR="$(mktemp -d)"
DB_CREATED=0

cleanup() {
    rm -rf -- "$MEDIA_DRILL_DIR"
    if [ "$DB_CREATED" -eq 1 ]; then
        echo "[drill] 訓練用データベースを片付けます..."
        docker compose exec -T db \
            psql -U "$POSTGRES_USER" -d postgres \
            -c "DROP DATABASE IF EXISTS $DRILL_DB;" > /dev/null
    fi
}
trap cleanup EXIT

echo "[drill] 訓練用データベースを作ります: $DRILL_DB"
docker compose exec -T db \
    psql -U "$POSTGRES_USER" -d postgres \
    -c "DROP DATABASE IF EXISTS $DRILL_DB;" > /dev/null
docker compose exec -T db \
    psql -U "$POSTGRES_USER" -d postgres \
    -c "CREATE DATABASE $DRILL_DB;" > /dev/null
DB_CREATED=1

echo "[drill] 平文を保存せず、復号ストリームを復元します..."
decrypt < "$DUMP" \
    | docker compose exec -T db \
        sh -c "pg_restore -U '$POSTGRES_USER' -d '$DRILL_DB' --no-owner"

echo "[drill] 復元した中身を数えます..."
docker compose exec -T db \
    psql -U "$POSTGRES_USER" -d "$DRILL_DB" -tA -c "
        SELECT 'articles=' || (SELECT count(*) FROM blog_article)
            || ' users='    || (SELECT count(*) FROM accounts_user)
            || ' comments=' || (SELECT count(*) FROM comments_comment)
            || ' media='    || (SELECT count(*) FROM media_library_mediaasset);
    "

echo "[drill] mediaを使い捨てディレクトリへ復元します..."
decrypt < "$MEDIA" | tar -xzf - -C "$MEDIA_DRILL_DIR"
if [ ! -d "$MEDIA_DRILL_DIR/media" ]; then
    echo "[drill] mediaディレクトリを復元できませんでした" >&2
    exit 1
fi
find "$MEDIA_DRILL_DIR/media" -type f -print0 | xargs -0 -r sha256sum > /dev/null

echo
echo "[drill] 完了。DBとmediaを復元し、暗号文のHMACも検証しました。"
