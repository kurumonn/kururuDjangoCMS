#!/bin/sh
# コンテナ起動時に、アプリを動かせる状態まで整えてから CMD を実行する。
#
# set -e を付けているのが要点。
# 途中の migrate が失敗しても Gunicorn が起動してしまうと、
# 「サービスは 200 を返すが、テーブルが古いまま」という
# 最も気づきにくい壊れ方をする。失敗したら起動しない。
set -e

echo "[entrypoint] データベースの受け入れ準備を待ちます..."
# db コンテナはプロセスが起動しても、すぐに接続を受け付けるとは限らない。
# compose の depends_on は「起動した」までしか保証しないので、ここで待つ。
python - <<'PY'
import os
import sys
import time

import psycopg

role_prefix = (
    "POSTGRES_MIGRATION"
    if os.environ.get("APP_START_MODE") == "migrate"
    else "POSTGRES_APP"
)
dsn = (
    f"host={os.environ.get('POSTGRES_HOST', 'db')} "
    f"port={os.environ.get('POSTGRES_PORT', '5432')} "
    f"dbname={os.environ['POSTGRES_DB']} "
    f"user={os.environ[f'{role_prefix}_USER']} "
    f"password={os.environ[f'{role_prefix}_PASSWORD']}"
)

deadline = time.time() + 60
while True:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            break
    except Exception as exc:  # 接続できない理由は問わず、時間内なら待つ
        if time.time() > deadline:
            print(f"[entrypoint] データベースへ接続できません: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)
print("[entrypoint] データベースに接続できました。")
PY

echo "[entrypoint] 本番向けの設定を検査します..."
# --deploy を付けると、本番で危険な設定を検査してくれる。
# accounts/checks.py の自作チェックもここで走る。
# 起動のたびに実行するのは、テストと違って「実行し忘れ」が起きないため。
python manage.py check --deploy --fail-level WARNING

APP_START_MODE="${APP_START_MODE:-web}"

case "$APP_START_MODE" in
    migrate)
        # Compose の one-shot release job だけが永続状態を変更する。
        echo "[entrypoint] マイグレーションを1回だけ適用します..."
        python manage.py migrate --noinput
        echo "[entrypoint] 静的ファイルを集めます..."
        python manage.py collectstatic --noinput
        echo "[entrypoint] release job が完了しました。"
        ;;
    web|worker)
        # 通常の web/worker 再起動は DB や static を変更しない。
        # 未適用 migration があれば fail-closed で起動を止める。
        echo "[entrypoint] 未適用マイグレーションが無いことを確認します..."
        python manage.py migrate --check
        echo "[entrypoint] 起動します: $*"
        exec "$@"
        ;;
    *)
        echo "[entrypoint] 不正な APP_START_MODE です: $APP_START_MODE" >&2
        exit 64
        ;;
esac
