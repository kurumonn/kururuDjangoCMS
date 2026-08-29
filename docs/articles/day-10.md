# 【10日目】Django CMS 完成――テスト・Docker・PostgreSQL・Redis・本番設定

> 連載「10日で作る Django CMS」の10日目です。
> 成果物: <https://github.com/kurumonn/DjangoCMS>（タグ `day-10`）

---

## 1. 今日の結論

CMS そのものは9日目までで動いています。今日やるのは、
**それを「自分の開発機以外でも動くもの」にする**ことです。

- 設定を環境ごとに分ける（`base` / `local` / `test` / `production`）
- 本番は PostgreSQL と Redis を使う
- Docker Compose で nginx / web / db / redis の4つを組む
- HTTPS 強制・HSTS・セキュリティヘッダー
- バックアップと、**復元の訓練**

**今日いちばん大事なのは、最後の「復元の訓練」です。**

バックアップは「取れているか」ではなく「戻せるか」でしか価値が測れません。
そして戻せないと分かるのは、たいてい本当に必要になったときです。

そして今日、この連載でいちばん痛い間違いが見つかります。
**8日目からずっと `requirements.txt` が間違っていました。**
テスト290件が全部通っている状態で、です。
詳しくは「8. よくあるエラー」で書きます。

---

## 2. 今日の完成画面

今日は画面が変わりません。変わるのは動かし方です。

```bash
docker compose up -d --build
```

```text
SERVICE   STATUS
db        Up 3 minutes (healthy)
nginx     Up 3 minutes
redis     Up 3 minutes (healthy)
web       Up 45 seconds (healthy)
```

構成はこうなります。

```text
        インターネット
             │
             ▼
        ┌─────────┐
        │  nginx  │  ← 80番だけが外に開いている
        └────┬────┘
             │ /static/  /media/  … nginx が自分で返す
             │ それ以外  … web へ転送
             ▼
        ┌─────────┐
        │   web   │  Django + Gunicorn（ワーカー3本）
        └──┬───┬──┘
           │   │
     ┌─────┘   └─────┐
     ▼               ▼
┌─────────┐    ┌─────────┐
│   db    │    │  redis  │
│Postgres │    │キャッシュ│
└─────────┘    │セッション│
               │レート制限│
               └─────────┘
```

`db` と `redis` と `web` は、**ホストにポートを公開していません**。
外から届くのは nginx の80番だけです。

---

## 3. 今日変更するファイル

| ファイル | 何をするか |
| --- | --- |
| `config/settings/__init__.py` | 新規。**わざと空にする** |
| `config/settings/base.py` | 旧 `settings.py` から共通部分を移す |
| `config/settings/local.py` | 新規。開発機用 |
| `config/settings/test.py` | 新規。テスト用 |
| `config/settings/production.py` | 新規。本番用 |
| `config/settings.py` | 削除 |
| `manage.py` | 既定を `config.settings.local` に |
| `config/wsgi.py` / `asgi.py` | 既定を `config.settings.production` に |
| `config/urls.py` | `/healthz/` を追加 |
| `core/views.py` | 新規。死活監視 |
| `core/checks.py` | 新規。HSTS とプロキシ設定の検査 |
| `core/apps.py` | チェックを登録 |
| `Dockerfile` | 新規。2段構成 |
| `compose.yaml` | 新規。4コンテナ |
| `compose.local-check.yaml` | 新規。手元で画面を見るための上書き |
| `docker/entrypoint.sh` | 新規。起動前の準備 |
| `docker/healthcheck.py` | 新規。死活監視の呼び出し側 |
| `docker/nginx/default.conf` | 新規 |
| `docker/nginx/local-check.conf` | 新規。検証専用 |
| `.env.example` | 新規。必要な環境変数の一覧 |
| `.dockerignore` | 新規 |
| `scripts/backup.sh` | 新規 |
| `scripts/restore_drill.sh` | 新規。復元の訓練 |
| `requirements.txt` | psycopg / redis / gunicorn を追加、依存の書き方を修正 |
| `config/tests/test_production_settings.py` | 新規 |
| `core/tests/test_checks.py` | 新規 |
| `core/tests/test_healthz.py` | 新規 |

---

## 4. 完成コード

### 4.1 設定パッケージ

まず `config/settings.py` を削除し、`config/settings/` を作ります。

`config/settings/__init__.py`:

```python
"""設定パッケージ。

このファイルは**わざと空にしてある**。

`config.settings` を import できるようにすると、
「どの環境の設定で動いているか分からないまま動く」状態が生まれる。
本番で `manage.py migrate` を打ったつもりが開発用の SQLite を
書き換えていた、という事故はこれで起きる。

そのため、必ず次のどれかを明示する。

    DJANGO_SETTINGS_MODULE=config.settings.local
    DJANGO_SETTINGS_MODULE=config.settings.production
"""
```

`config/settings/base.py` は、9日目までの `settings.py` から
**環境ごとに変わるものを抜いた**残りです。冒頭だけ載せます。

```python
import os
from pathlib import Path

# このファイルは config/settings/base.py なので、3つ上まで遡る。
# （9日目までは config/settings.py だったので parent が1つ少なかった。
#   分割したときにここを直し忘れると、静的ファイルの場所が1階層ずれる。）
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env_bool(name: str, default: bool) -> bool:
    """環境変数を真偽値として読む。

    "1" だけを真として扱う。"true" や "yes" も許すと、
    "True" と書いたつもりの "ture" が静かに False になる。
    書き方を1つに絞ると、間違えたときに必ず False になって気づける。
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip() == "1"


def env_list(name: str, default: str = "") -> list[str]:
    """カンマ区切りの環境変数をリストにする。空要素は捨てる。"""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]
```

`INSTALLED_APPS` 以降は9日目までと同じなので省略します
（[`config/settings/base.py`](https://github.com/kurumonn/DjangoCMS/blob/main/config/settings/base.py) を参照）。

`config/settings/local.py`:

```python
from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env_bool

DEBUG = True

# 開発用の固定キー。
# "django-insecure-" で始めているのは Django の慣習で、
# `manage.py check --deploy` がこの接頭辞を見て警告を出してくれるため。
SECRET_KEY = "django-insecure-dev-only-do-not-use-in-production"  # pragma: allowlist secret

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

database_role_prefix = (
    "POSTGRES_MIGRATION"
    if os.environ.get("APP_START_MODE") == "migrate"
    else "POSTGRES_APP"
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kururucms-local",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# WebAuthn は HTTPS でしか動かない（localhost だけ例外）。
# 開発中は緩める必要があるが、DEBUG と連動させてはいけない。
# DEBUG は環境変数の書き忘れで本番でも True になることがあり、
# そのとき一緒にこの保護まで外れてしまうため、独立した変数にしておく。
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = env_bool("DJANGO_MFA_ALLOW_INSECURE_ORIGIN", True)
```

`config/settings/production.py`:

```python
import os

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env_bool, env_list

DEBUG = False


def require(name: str) -> str:
    """本番で必須の環境変数を読む。無ければ起動を止める。

    既定値を用意しないのが要点。
    たとえば SECRET_KEY に開発用の値を入れておくと、
    設定を忘れたまま起動し、セッション署名が公開鍵で行われる。
    落ちてくれた方が安全なので、落とす。
    """
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"環境変数 {name} が未設定です。本番では必ず指定してください。"
            " 設定すべき項目の一覧は .env.example にあります。"
        )
    return value


SECRET_KEY = require("DJANGO_SECRET_KEY")

# ALLOWED_HOSTS を空のままにすると、DEBUG=False では全リクエストが 400 になる。
# 「本番に上げたら真っ白」の原因で最も多いもののひとつ。
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise RuntimeError(
        "DJANGO_ALLOWED_HOSTS が未設定です。"
        " 例: DJANGO_ALLOWED_HOSTS=cms.example.com,www.example.com"
    )

# CSRF の検証はスキーム込みで行う。ホスト名だけでは足りない。
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS") or [
    f"https://{host}" for host in ALLOWED_HOSTS
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": require("POSTGRES_DB"),
        "USER": require(f"{database_role_prefix}_USER"),
        "PASSWORD": require(f"{database_role_prefix}_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # 接続を使い回す秒数。0 だとリクエストごとに接続し直す。
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        # 使い回す接続が生きているか、使う前に確かめる。
        # これが無いと、DB を再起動した直後の数リクエストが必ず失敗する。
        "CONN_HEALTH_CHECKS": True,
    }
}

# Redis を共有キャッシュにする理由は速度ではなく、**正しさ**。
#
# allauth のレート制限は回数をキャッシュに記録する。
# ローカルメモリだとワーカーごとに別の数え方になるので、
# 「5回まで」がワーカー4本で実質20回になる。
# ログインの総当たり制限が4倍緩むということなので、これは性能の話ではない。
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# 保存先を Redis だけにすると再起動で全員ログアウトになるため、
# 読みはキャッシュ・書きは DB にも残す cached_db を使う。
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# Nginx が HTTPS を終端し、Django へは HTTP で渡す構成を前提にする。
# このヘッダーを見ないと、Django は常に「HTTP で来た」と判断し、
# SECURE_SSL_REDIRECT が無限リダイレクトを起こす。
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

SILENCED_SYSTEM_CHECKS = []
if not SECURE_HSTS_INCLUDE_SUBDOMAINS:
    SILENCED_SYSTEM_CHECKS.append("security.W005")
if not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS.append("security.W021")

TRUSTED_PROXY_COUNT = int(os.environ.get("DJANGO_TRUSTED_PROXY_COUNT", "1"))

# 本番でパスキーの origin 検査を緩めることは無い。環境変数からも読まない。
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False

# 内容のハッシュをファイル名に含める。
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
}

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = require("DJANGO_EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10  # 秒。無いと SMTP が応答しないときリクエストが固まる。
```

ログ設定は長いので省略します
（[`production.py`](https://github.com/kurumonn/DjangoCMS/blob/main/config/settings/production.py) を参照）。

`config/settings/test.py`:

```python
from .local import *  # noqa: F401,F403

# パスワードハッシュを最速のものに差し替える。
# Argon2 は意図的に遅いので、ユーザーを作るテストが何百件もあると
# それだけで数分かかる。
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# テストごとにキャッシュを分ける。
# レート制限の回数はキャッシュに残るので、共有すると
# 「単体では通るのに、まとめて実行すると落ちる」テストができる。
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kururucms-test",
    }
}

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = True
```

### 4.2 エントリポイントの既定値

`manage.py`:

```python
def main():
    # 開発コマンドの既定は開発用設定。
    # 本番サーバーで manage.py を叩くときは DJANGO_SETTINGS_MODULE を明示する。
    # 忘れると、本番機の上で開発用の SQLite を migrate してしまい、
    # 「成功と出たのに本番のデータベースは何も変わっていない」状態になる。
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
```

`config/wsgi.py` と `config/asgi.py`:

```python
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.production")
```

### 4.3 死活監視

`core/views.py`:

```python
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
def healthz(request):
    """死活監視用のエンドポイント。

    「プロセスが生きているか」ではなく「**仕事ができる状態か**」を返す。
    Gunicorn は起動していてもデータベースへ繋がらなければ、
    利用者から見れば落ちているのと同じだからである。

    逆に、ここで重い処理をしてはいけない。
    監視は数秒おきに叩かれるので、記事数を数えるような処理を入れると
    それ自体が負荷になる。SELECT 1 で十分。
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:
        # 例外の中身は返さない。
        # 接続文字列やホスト名が漏れるおそれがあるため、外向きには状態だけ返す。
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)

    return JsonResponse({"status": "ok"})
```

`config/urls.py` の先頭へ追加します。

```python
from core.views import healthz

urlpatterns = [
    # 死活監視。compose の healthcheck と、デプロイ編の監視から叩かれる。
    # 認証を掛けていないのは、監視側に認証情報を持たせたくないため。
    # 返すのは状態だけで、内部の情報は含めない。
    path("healthz/", healthz, name="healthz"),
    ...
]
```

### 4.4 Dockerfile

```dockerfile
# --- 1段目: 依存パッケージを wheel にする -----------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install --no-install-recommends -y build-essential libpq-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels
COPY requirements.txt .
RUN pip wheel --wheel-dir /wheels -r requirements.txt

# --- 2段目: 実行用 ----------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

RUN apt-get update \
 && apt-get install --no-install-recommends -y libpq5 \
 && rm -rf /var/lib/apt/lists/*

# root で動かさない。
# コンテナが乗っ取られたとき、root だとホスト側への影響が桁違いに大きくなる。
RUN useradd --create-home --uid 10001 kururu

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt \
 && rm -rf /wheels

COPY --chown=kururu:kururu . .

RUN mkdir -p /app/staticfiles /app/media \
 && chown -R kururu:kururu /app/staticfiles /app/media

USER kururu

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
```

### 4.5 起動スクリプト

`docker/entrypoint.sh`:

```sh
#!/bin/sh
# set -e を付けているのが要点。
# 途中の migrate が失敗しても Gunicorn が起動してしまうと、
# 「サービスは 200 を返すが、テーブルが古いまま」という
# 最も気づきにくい壊れ方をする。失敗したら起動しない。
set -e

echo "[entrypoint] データベースの受け入れ準備を待ちます..."
# db コンテナはプロセスが起動しても、すぐに接続を受け付けるとは限らない。
# compose の depends_on は「起動した」までしか保証しないので、ここで待つ。
python - <<'PY'
import os, sys, time
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
    except Exception as exc:
        if time.time() > deadline:
            print(f"[entrypoint] データベースへ接続できません: {exc}", file=sys.stderr)
            sys.exit(1)
        time.sleep(1)
print("[entrypoint] データベースに接続できました。")
PY

echo "[entrypoint] 本番向けの設定を検査します..."
# 起動のたびに実行するのは、テストと違って「実行し忘れ」が起きないため。
python manage.py check --deploy --fail-level WARNING

echo "[entrypoint] マイグレーションを適用します..."
python manage.py migrate --noinput

echo "[entrypoint] 静的ファイルを集めます..."
python manage.py collectstatic --noinput

echo "[entrypoint] 起動します: $*"
exec "$@"
```

### 4.6 compose.yaml

2026年8月のセキュリティ更新では、PostgreSQLの資格情報を管理者、
マイグレーション所有者、Web/worker用DML利用者の3つへ分離しました。
下記は要点だけの抜粋です。実際のイメージはタグだけでなくdigestまで固定します。

```yaml
services:
  db:
    image: postgres:17-alpine@sha256:<確認済みdigest>
    restart: unless-stopped
    env_file:
      - .env
      - .env.db-admin
    volumes:
      - pgdata:/var/lib/postgresql/data
    # ポートを外へ出さない。
    # 5432 をホストへ公開すると、そのサーバーのファイアウォール次第で
    # インターネットから直接データベースへ届く。
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10

  db_role_provision:
    image: postgres:17-alpine@sha256:<確認済みdigest>
    restart: "no"
    env_file:
      - .env
      - .env.db-migration
      - .env.db-admin
    command: ["/bin/sh", "/scripts/provision_db_role.sh"]
    depends_on:
      db:
        condition: service_healthy

  migrate:
    build: .
    restart: "no"
    env_file:
      - .env
      - .env.db-migration
    environment:
      APP_START_MODE: "migrate"
    depends_on:
      db_role_provision:
        condition: service_completed_successfully

  redis:
    image: redis:8-alpine
    restart: unless-stopped
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redisdata:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  web:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    depends_on:
      migrate:
        condition: service_completed_successfully
      redis:
        condition: service_healthy
    volumes:
      - staticfiles:/app/staticfiles
      - media:/app/media
    healthcheck:
      test: ["CMD", "python", "/app/docker/healthcheck.py"]
      interval: 15s
      timeout: 5s
      retries: 5
      start_period: 40s

  nginx:
    image: nginx:1.29-alpine
    restart: unless-stopped
    depends_on:
      - web
    ports:
      - "80:80"
    volumes:
      - ./docker/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      # 読み取り専用でマウントする。
      - staticfiles:/var/www/static:ro
      - media:/var/www/media:ro

volumes:
  pgdata:
  redisdata:
  staticfiles:
  media:
```

`depends_on` に `condition: service_healthy` を付けているのが要点です。
これが無いと、web は db の**プロセスが起動した瞬間**に動き出します。
PostgreSQL は起動してから接続を受け付けるまでに数秒あるので、
その間の migrate は失敗します。

### 4.7 nginx

```nginx
upstream django {
    server web:8000;
}

server {
    listen 80;
    server_name _;

    # Django 側の DATA_UPLOAD_MAX_MEMORY_SIZE と揃える。
    client_max_body_size 6m;

    # nginx のバージョンを隠す。
    server_tokens off;

    location /static/ {
        alias /var/www/static/;
        access_log off;
        # ManifestStaticFilesStorage がファイル名に内容のハッシュを付けるので、
        # 内容が変われば URL も変わる。つまり長期キャッシュが安全にできる。
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias /var/www/media/;
        access_log off;
        expires 7d;

        # ★重要★ アップロードされたファイルを絶対に実行させない。
        # 4日目に中身で検証しているとはいえ、多層で守る。
        add_header X-Content-Type-Options nosniff;
        add_header Content-Security-Policy "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'";
        location ~ \.(php|phtml|py|pl|cgi|sh)$ {
            return 404;
        }
    }

    location / {
        proxy_pass http://django;

        # 特に X-Forwarded-Proto は、これが無いと Django が
        # 常に HTTP 扱いにして SECURE_SSL_REDIRECT が無限ループする。
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_redirect off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
    }
}
```

### 4.8 バックアップと復元の訓練

`scripts/backup.sh` の要点だけ抜き出します（全文は
[リポジトリ](https://github.com/kurumonn/DjangoCMS/blob/main/scripts/backup.sh)）。

```bash
echo "[backup] データベースを書き出します..."
# -Fc はカスタム形式。テキストの SQL より小さく、
# pg_restore で「テーブル単位の復元」ができる。
docker compose exec -T db \
    pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc \
    > "$DB_DUMP"

# ★ここが本題★
# 「バックアップを取った」ではなく「**戻せる**」ことを確かめる。
# 取れているつもりのファイルが 0 バイトだった、という事故は珍しくない。
if [ ! -s "$DB_DUMP" ]; then
    echo "[backup] 失敗: データベースのダンプが空です" >&2
    exit 1
fi
# pg_restore --list は、ダンプの目次を読むだけで復元はしない。
# 壊れたダンプならここで失敗する。
docker compose exec -T db sh -c 'pg_restore --list' < "$DB_DUMP" > /dev/null
```

`scripts/restore_drill.sh` は、使い捨ての別データベースへ復元します。

```bash
DRILL_DB="kururucms_restore_drill"

# 本番の DB 名と同じ名前を使っていないことを確かめる。
# ここを間違えると訓練が事故になる。
if [ "$DRILL_DB" = "$POSTGRES_DB" ]; then
    echo "[drill] 中止: 訓練用の名前が本番と同じです" >&2
    exit 1
fi

cleanup() {
    docker compose exec -T db \
        psql -U "$POSTGRES_USER" -d postgres \
        -c "DROP DATABASE IF EXISTS $DRILL_DB;" > /dev/null
}
# 途中で失敗しても必ず片付ける。
# 残しておくと、次回「既にある」で失敗し、訓練そのものをやらなくなる。
trap cleanup EXIT
```

---

## 5. コードの意味

### 設定の分割

| 書き方 | 意味 |
| --- | --- |
| `from .base import *` | base の設定をすべて取り込む |
| `from .base import BASE_DIR, env_bool` | `*` では入らない名前を明示的に取り込む |
| `config/settings/__init__.py` が空 | `config.settings` を設定モジュールとして使えなくする |

`from .base import *` の後に `from .base import BASE_DIR` を
もう一度書いているのは、静的解析（flake8 など）のためです。
`import *` で入った名前は解析ツールから見えないので、
使うものは明示しておくと未定義エラーを出されずに済みます。

### production.py

| 書き方 | 意味 |
| --- | --- |
| `require("DJANGO_SECRET_KEY")` | 無ければ `RuntimeError` で起動を止める |
| `CONN_MAX_AGE` | DB 接続を使い回す秒数 |
| `CONN_HEALTH_CHECKS` | 使い回す前に接続が生きているか確かめる |
| `SECURE_PROXY_SSL_HEADER` | どのヘッダーで HTTPS だと判断するか |
| `SECURE_SSL_REDIRECT` | HTTP で来たら HTTPS へ 301 |
| `SECURE_HSTS_SECONDS` | ブラウザーに HTTPS 固定を覚えさせる秒数 |
| `SESSION_ENGINE = "...cached_db"` | 読みはキャッシュ、書きは DB にも残す |
| `ManifestStaticFilesStorage` | 静的ファイル名に内容のハッシュを付ける |
| `SILENCED_SYSTEM_CHECKS` | 指定した検査を黙らせる |

### Dockerfile

| 書き方 | 意味 |
| --- | --- |
| `FROM ... AS builder` | 1段目に名前を付ける |
| `COPY --from=builder` | 1段目の成果物だけを持ってくる |
| `PYTHONDONTWRITEBYTECODE=1` | `.pyc` を書かない |
| `PYTHONUNBUFFERED=1` | 出力をためこまない（ログが遅れない） |
| `useradd --uid 10001` | root 以外の実行ユーザーを作る |
| `USER kururu` | ここから先は非 root で動く |
| `ENTRYPOINT` + `CMD` | ENTRYPOINT が準備し、最後に CMD を exec する |

`COPY requirements.txt .` を `COPY . .` より先に書いているのは、
**Docker のキャッシュを効かせる**ためです。
アプリのコードを1文字直すたびに依存を入れ直すと、
ビルドが毎回数分かかります。

### compose.yaml

| 書き方 | 意味 |
| --- | --- |
| `condition: service_healthy` | 相手が healthy になるまで起動しない |
| `restart: unless-stopped` | 落ちたら再起動する（手で止めたときは除く） |
| `volumes: - staticfiles:/app/staticfiles` | 名前付きボリュームを共有する |
| `:ro` | 読み取り専用でマウントする |
| `$${POSTGRES_USER}` | `$$` は compose のエスケープ。コンテナ内で展開させる |

---

## 6. 内部で起きていること

### なぜ Redis が「速度ではなく正しさ」の話になるのか

8日目に、ログインのレート制限を入れました。

```python
ACCOUNT_RATE_LIMITS = {
    "login_failed": "5/5m/ip,3/5m/key",
}
```

この「5回」という数字は、どこかに記録しないと数えられません。
allauth はそれを Django のキャッシュへ入れます。

開発中はプロセスが1つなので、ローカルメモリで足ります。

しかし本番の Gunicorn はワーカーを3本立てます。
`LocMemCache` は**プロセスごとに別の辞書**なので、こうなります。

```text
ワーカー1: 失敗2回
ワーカー2: 失敗2回     合計6回試せている
ワーカー3: 失敗2回     （どのワーカーも「5回まで」に達していない）
```

リクエストがどのワーカーへ行くかは分からないので、
攻撃者は何もしなくても、ワーカー数の分だけ多く試せます。

つまりこれは「Redis を入れると速くなる」話ではなく、
**Redis を入れないとレート制限が設計どおりに効かない**という話です。

セッションを `cached_db` にしているのも似た理由で、
ワーカーごとにセッションが違うと、ログインしたりしなかったりします。

### HSTS が「取り消せない」とはどういうことか

HTTPS で応答するとき、こういうヘッダーを送ります。

```text
Strict-Transport-Security: max-age=31536000
```

ブラウザーはこれを見て、**そのドメインを1年間 HTTPS 固定で記憶します**。
以後、利用者が `http://` と打っても、ブラウザーが勝手に `https://` にします。

中間者が HTTP へ落として盗聴する攻撃を防げるので、有効な仕組みです。

問題は、こちらから取り消せないことです。

証明書が切れたとします。ふつうなら「とりあえず HTTP で見せて、
その間に証明書を直す」ができます。しかし HSTS を送った後は、
ブラウザーが HTTPS を強制するので、利用者には
**回避ボタンの無いエラー画面**が出ます。
`max-age` が切れるまで、その状態が続きます。

`includeSubDomains` を付ければ、影響がサブドメイン全部に及びます。
`preload` に登録すると、ブラウザー本体に焼き込まれるので、
取り消しに数か月かかります。

だから段階的に上げます。

```text
3600（1時間）
   ↓ 全ページが HTTPS で問題なく動くことを確認
604800（1週間）
   ↓ 数日運用して問題が出ないことを確認
31536000（1年）
   ↓ サブドメインもすべて HTTPS で出せることを確認
includeSubDomains
   ↓ 本当に後戻りしないと決めてから
preload
```

`manage.py check --deploy` は「今すぐ全部 True にしろ」と言ってきますが、
これは静的な検査が運用の途中経過を表現できないだけです。
詳しくは「8. よくあるエラー」の1番に書きました。

### コンテナのヘルスチェックが自滅する仕組み

compose の healthcheck は、**コンテナの中で**実行されます。
つまり nginx を通りません。

```text
【本番のリクエスト】
利用者 → nginx → web
                 Host: cms.example.com
                 X-Forwarded-Proto: https

【healthcheck のリクエスト】
web の中 → web
           Host: 127.0.0.1:8000       ← ALLOWED_HOSTS に無い → 400
           X-Forwarded-Proto: 無し     ← HTTP 扱い → 301
```

正しく設定してあるからこそ、両方に引っかかります。

ここで `SECURE_SSL_REDIRECT` を切ったり、
`ALLOWED_HOSTS` に `*` を足したりすると、
**監視の都合で本番の防御を下げる**ことになります。

正しいのは、監視側が本番と同じ形のリクエストを送ることです。

```python
request = urllib.request.Request(
    "http://127.0.0.1:8000/healthz/",
    headers={
        "Host": first_allowed_host(),      # ALLOWED_HOSTS 対策
        "X-Forwarded-Proto": "https",      # SECURE_SSL_REDIRECT 対策
    },
)
```

---

## 7. コマンドの説明

### `docker compose up -d --build`

| 項目 | 内容 |
| --- | --- |
| 目的 | イメージを作り直して、4つのコンテナを起動する |
| 実行場所 | `compose.yaml` があるディレクトリ |
| 正常例 | `Container djangocms-web-1 Started` |
| 異常例 | `env file .../.env not found` |
| 判断方法 | `docker compose ps` で全部 `healthy` になること |

`-d` はバックグラウンド実行、`--build` はイメージの作り直しです。
`--build` を忘れると、コードを直しても古いイメージのまま起動します。

### `docker compose ps`

| 項目 | 内容 |
| --- | --- |
| 目的 | 各コンテナの状態を見る |
| 正常例 | `Up 3 minutes (healthy)` |
| 異常例 | `Up 2 minutes (health: starting)` のまま変わらない |
| 判断方法 | `health: starting` が続くなら `docker compose logs web` を見る |

`(healthy)` が付くのは healthcheck を書いたサービスだけです。
nginx には書いていないので `Up` だけになります。

### `docker compose logs web --tail 40`

| 項目 | 内容 |
| --- | --- |
| 目的 | web コンテナのログを見る |
| 実行場所 | 同上 |
| 正常例 | `[INFO] Listening at: http://0.0.0.0:8000` |
| 異常例 | `ModuleNotFoundError` / `django.db.utils.OperationalError` |
| 判断方法 | entrypoint のどの段階まで進んだかで切り分ける |

`entrypoint.sh` が段階ごとにメッセージを出すので、
どこで止まったかがログだけで分かります。

### `python manage.py check --deploy`

| 項目 | 内容 |
| --- | --- |
| 目的 | 本番で危険な設定を検出する |
| 実行場所 | プロジェクトルート（本番設定を指定して） |
| 正常例 | `System check identified 1 issue (2 silenced).`（Info のみ） |
| 異常例 | `SystemCheckError: ...` |
| 判断方法 | 終了コードが 0 かどうか |

`--fail-level WARNING` を付けると、警告でも失敗扱いになります。
`entrypoint.sh` はこれを使い、危険な設定のまま起動しないようにしています。

### `./scripts/backup.sh`

| 項目 | 内容 |
| --- | --- |
| 目的 | DB と画像のバックアップを取り、**壊れていないか確かめる** |
| 実行場所 | プロジェクトルート（コンテナが動いている状態で） |
| 正常例 | `[backup] ダンプの目次を読めました（壊れていません）。` |
| 異常例 | `pg_restore: error: did not find magic string in file header` |
| 判断方法 | 最後に出るファイルサイズが 0 でないこと |

### `./scripts/restore_drill.sh <ダンプ>`

| 項目 | 内容 |
| --- | --- |
| 目的 | 本番に触れずに、復元できることを確かめる |
| 実行場所 | 同上 |
| 正常例 | 復元側と本番側の件数がほぼ一致する |
| 異常例 | 復元側が `articles=0` |
| 判断方法 | 2行を見比べる。差はバックアップ以降に増えた分だけのはず |

---

## 8. よくあるエラー

実際に手が止まったものだけを書きます。全9件は
[`docs/errors/day-10.md`](https://github.com/kurumonn/DjangoCMS/blob/main/docs/errors/day-10.md)
にあります。ここでは特に効いた4件を。

### 8.1 依存の宣言が、8日目からずっと間違っていた

イメージはビルドできたのに、コンテナが起動しませんでした。

```text
web-1  | [entrypoint] データベースに接続できました。
web-1  | [entrypoint] 本番向けの設定を検査します...
web-1  |   File ".../allauth/socialaccount/providers/google/provider.py", line 3, in <module>
web-1  |     import requests
web-1  | ModuleNotFoundError: No module named 'requests'
```

`requirements.txt` に `requests` が入っていませんでした。

**この間違いは8日目からありました。**
気づかなかったのは、開発機の仮想環境に `requests` が
別の経路で入っていたからです。
`pip install -r requirements.txt` だけで環境を作った人は、
8日目の時点で動かなかったはずです。

原因は、依存を手で書き写したこと。

```text
django-allauth==65.18.0
# django-allauth[mfa] が引く依存。9日目で使う。
qrcode==8.2
fido2==2.2.1
```

`qrcode` と `fido2` を自分で並べた時点で、
「allauth が必要とするもの」を自分の理解で列挙したことになります。
上流が依存を増やしても、こちらの一覧は増えません。

直し方は、パッケージ側に決めさせることです。

```text
django-allauth[mfa,socialaccount]==65.18.0
```

角かっこは追加機能（extras）の指定で、
`mfa` が `qrcode` と `fido2` を、
`socialaccount` が `requests` などを連れてきます。

**そして重要なのは、この間違いをテストでは見つけられないことです。**
テストは290件すべて通っていました。
見つかったのは、Docker が「まっさらな環境で入れ直す」からです。

似た間違いをもう1件、同じ場所で見つけています。
`redis==6.5.0` と書いていましたが、その版は存在しませんでした
（6系は 6.4.0 で終わっています）。
これも手元では `redis` が既に入っていたので気づけませんでした。

| | 間違い | 手元で気づけたか |
| --- | --- | --- |
| 1 | 存在しないバージョンを書いた | ❌ 既に入っていた |
| 2 | 必要な依存を書き忘れた | ❌ 別経由で入っていた |

**動いている環境は、依存関係の宣言が正しい証拠になりません。**

### 8.2 `check --deploy` の警告が、正しい運用手順とぶつかる

```text
$ python manage.py check --deploy --fail-level WARNING
SystemCheckError: System check identified some issues:

WARNINGS:
?: (security.W005) You have not set the SECURE_HSTS_INCLUDE_SUBDOMAINS setting to True. ...
?: (security.W021) You have not set the SECURE_HSTS_PRELOAD setting to True. ...
```

Django の言い分は正しいのですが、
「6. 内部で起きていること」に書いたとおり、HSTS は取り消せません。
警告に従って今すぐ全部 True にするのは危険です。

黙らせますが、**黙らせっぱなしにはしません**。

```python
SILENCED_SYSTEM_CHECKS = []
if not SECURE_HSTS_INCLUDE_SUBDOMAINS:
    SILENCED_SYSTEM_CHECKS.append("security.W005")
if not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS.append("security.W021")
```

True にすれば警告の対象から外れるので、この行も自然に効かなくなります。
そのうえで、代わりのチェックを自作しました。

```text
INFOS:
?: (core.I001) HSTS: max-age=3600 秒 / includeSubDomains=False / preload=False
	HINT: 1時間。まず HTTPS が全ページで問題なく動くことを確かめる段階。
	      次にやること: 問題が無ければ DJANGO_SECURE_HSTS_SECONDS=604800 へ上げる
```

起動のたびに、今どの段階にいて次に何をするかが出ます。

`Warning` ではなく `Info` にしているのは、
`--fail-level WARNING` で起動が止まると
「段階的に上げる」こと自体ができなくなるからです。

**警告を黙らせるときは、必ず代わりを置く。**
`SILENCED_SYSTEM_CHECKS` に足すだけの対処は、
「静かになった」以外に何も生みません。

### 8.3 `sys.modules` に残った設定モジュールで、テストが通ってしまう

本番設定のテストを書いたら、環境変数を消しても例外が出ませんでした。

```python
os.environ["DJANGO_SECRET_KEY"] = "..."
importlib.import_module("config.settings.production")   # 1回目

del os.environ["DJANGO_SECRET_KEY"]
importlib.import_module("config.settings.production")   # 2回目: 例外が出ない
```

Python はモジュールを**一度しか実行しません**。
2回目は `sys.modules` にあるものを返すだけです。

厄介なのは、これが**テストを緑にする方向**に働くことです。
「必須の環境変数が無いと落ちる」ことを確かめたいのに、
1回目で成功した結果が使い回されて、
「落ちなかった＝壊れている」のに気づけません。

```python
sys.modules.pop("config.settings.production", None)
module = importlib.import_module("config.settings.production")
```

このテストが本当に検査できているかは、わざと壊して確かめました。
`require()` を「無ければ空文字を返す」に変えて、
テストが**落ちること**を見ています。落ちなければ何も検査していません。

### 8.4 `pg_restore --list /dev/stdin` は必ず失敗する

バックアップの検証だけが通りませんでした。

```text
pg_restore: error: did not find magic string in file header
```

ダンプは壊れていません。先頭を見れば分かります。

```text
first 16 bytes: b'PGDMP\x01\x10\x00\x04\x08\x01\x01\x00\x14\x00\x00'
```

コンテナへ渡る途中でも壊れていませんでした
（中で `wc -c` するとローカルと同じ 104537 バイト）。

原因は渡し方です。
`pg_restore` はカスタム形式のアーカイブを読むときシークします。
`/dev/stdin` を**ファイル名として**渡すと普通のファイルとして開かれ、
シークしようとします。しかしパイプはシークできません。

3つ試して切り分けました。

| 書き方 | 結果 |
| --- | --- |
| `pg_restore --list /dev/stdin` | ❌ |
| `pg_restore --list`（標準入力） | ✅ |
| 一度コンテナ内へファイルとして置いてから `--list` | ✅ |

3つ目が通る時点で、ファイルの中身ではなく渡し方の問題だと分かります。

---

## 9. 動作確認

### チェックリスト

- [ ] `docker compose up -d --build` で4コンテナが起動する
- [ ] `docker compose ps` で db / redis / web が `(healthy)` になる
- [ ] `http://localhost/` が 200 を返す（`compose.local-check.yaml` 併用）
- [ ] レスポンスに `Strict-Transport-Security: max-age=3600` が付く
- [ ] `X-Frame-Options: DENY` と `X-Content-Type-Options: nosniff` が付く
- [ ] `Server: nginx`（版番号が出ていない）
- [ ] `/healthz/` が `{"status": "ok"}` を返し、キャッシュされない
- [ ] 静的ファイルがハッシュ付きの名前で、`Cache-Control: immutable` が付く
- [ ] `X-Forwarded-Proto: http` で叩くと 301 で https へ飛ぶ
- [ ] Redis に実際にキーが入る
- [ ] `./scripts/backup.sh` が「壊れていません」まで進む
- [ ] `./scripts/restore_drill.sh` の件数が本番と一致する
- [ ] 壊したダンプを検査が弾く
- [ ] `python manage.py test --settings=config.settings.test` が全件通る

### 実際に確認した結果

```text
$ docker compose ps
SERVICE   STATUS
db        Up 3 minutes (healthy)
redis     Up 3 minutes (healthy)
web       Up 45 seconds (healthy)
nginx     Up 3 minutes
```

```text
$ curl -sI http://localhost/
Server: nginx
X-Frame-Options: DENY
Strict-Transport-Security: max-age=3600
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
```

```text
$ curl -s http://localhost/healthz/
{"status": "ok"}
```

静的ファイルは nginx が返しています。

```text
$ curl -sI http://localhost/static/css/site.4b626d10f0f2.css
status=200 cache=public, max-age=31536000, immutable server=nginx
```

HTTPS へのリダイレクトも効いています。

```text
$ (コンテナ内から X-Forwarded-Proto: http で叩く)
301 -> https://localhost/
```

Redis に本当に入っているかは、Django 側から書いて Redis 側から読みました。

```text
cache backend: django.core.cache.backends.redis.RedisCache
read back: ok
session engine: django.contrib.sessions.backends.cached_db
db engine: django.db.backends.postgresql
DEBUG: False

$ docker compose exec redis redis-cli KEYS '*'
:1:probe-from-django
```

そして復元の訓練です。

```text
$ ./scripts/restore_drill.sh backups/db-20260805-170236.dump
[drill] 訓練用データベースを作ります: kururucms_restore_drill
[drill] 復元します...
[drill] 復元した中身を数えます...
articles=5 users=1 comments=0 media=0
[drill] 現在の本番と比べます...
articles=5 users=1 comments=0 media=0
[drill] 訓練用データベースを片付けます...
```

**この訓練が本当に壊れたバックアップを弾けるか**も確かめました。
ダンプを途中で切ったものと、空のファイルを検査させています。

```text
=== 壊れたダンプを検査させる（失敗するのが正しい） ===
OK: 壊れていることを検出した
=== 空のダンプ ===
OK: 空も検出した
```

検査が「何も検査していない」状態になっていないか、という確認です。
6日目にも同じことをしました。**通るテストより、落ちるべきときに落ちるテスト**です。

テストは252件から290件になりました。

```text
Ran 290 tests in 4.385s
OK
```

---

## 10. セキュリティ上の注意

### 今日入れた防御

| 対策 | 何を防ぐか |
| --- | --- |
| `require()` で必須の環境変数を強制 | 設定を忘れたまま公開すること |
| `SECURE_SSL_REDIRECT` | HTTP での通信 |
| HSTS | HTTP へ落とす中間者攻撃 |
| `SESSION_COOKIE_SECURE` / `CSRF_COOKIE_SECURE` | 平文でのクッキー送信 |
| Redis の共有キャッシュ | レート制限のワーカー分の緩み |
| 非 root ユーザーで実行 | コンテナ侵入時の被害拡大 |
| db / redis / web のポートを公開しない | インターネットからの直接接続 |
| `server_tokens off` | nginx の版番号からの脆弱性特定 |
| media 配下の CSP と拡張子拒否 | アップロードファイルの実行 |
| `.dockerignore` に `.env` | イメージへの秘密の焼き込み |

### `.env.example` のダミー値を、あえて日本語にした理由

```text
DJANGO_SECRET_KEY=ここに生成した値を貼る
```

英語でそれらしい文字列（`your-secret-key-here` など）を書くと、
**そのまま使われても動いてしまいます**。
「本番の署名鍵が、GitHub に公開されている example ファイルの文字列」
という状態になります。

日本語のダミーにしておけば、置き換えを忘れたことに気づきやすくなります。
`cp .env.example .env` して満足しない、という運用上の歯止めです。

### `check --deploy` を起動のたびに走らせる理由

テストは、開発者が実行しないと動きません。
「急いでいるから今日はテストを飛ばす」ができます。

システムチェックは `entrypoint.sh` の中にあるので、
**コンテナが起動する限り必ず走ります**。飛ばす方法がありません。

セキュリティに関わる検査は、飛ばせない場所に置くのが有効です。

### まだ足りないもの

10日目の時点では、HTTPS の証明書がありません。
`SECURE_SSL_REDIRECT` は有効ですが、リダイレクト先を用意していない状態です。
ここはデプロイ編（7日目）で Let's Encrypt を扱います。

ファイアウォール（デプロイ編3日目）と SSH 鍵（同2日目）も、
まだサーバー側の話として残っています。

---

## 11. 今日の復習問題

**問1.** 本番のキャッシュを `LocMemCache` のままにすると、
ログインのレート制限に何が起きますか。理由も答えてください。

**問2.** `SECURE_HSTS_SECONDS` をいきなり `31536000` にして、
その後で証明書が切れると何が起きますか。HTTP に戻して復旧できますか。

**問3.** `config/settings/__init__.py` を空のままにしているのはなぜですか。

**問4.** テストが290件すべて通っているのに、
`requirements.txt` の間違いに気づけなかったのはなぜですか。
どうすれば気づけますか。

**問5.** compose の healthcheck が 301 を返し続けたとき、
`SECURE_SSL_REDIRECT` を無効にして解決するのは、なぜ良くないのですか。

<details>
<summary>解答</summary>

**問1.**
`LocMemCache` はプロセスごとに別の辞書なので、Gunicorn のワーカー数だけ
制限が緩みます。ワーカー3本なら「5回まで」が実質15回試せます。
どのワーカーへ振られるかは攻撃者にも分からないため、
何も工夫しなくても自動的に緩みます。共有キャッシュ（Redis）にすれば、
どのワーカーが受けても同じ数え方になります。

**問2.**
ブラウザーはそのドメインを1年間 HTTPS 固定で記憶しているので、
利用者には回避ボタンの無いエラー画面が出ます。
HTTP へ戻して復旧することはできません。
サーバー側から「やっぱり HTTP でいい」と伝える手段が無く、
`max-age` が切れるまで待つしかないためです。
だから 3600 秒から始めて、段階的に上げます。

**問3.**
`config.settings` を設定モジュールとして使えるようにすると、
「どの環境の設定で動いているか分からないまま動く」状態が作れてしまいます。
本番サーバーで `manage.py migrate` を打ったつもりが
開発用の SQLite を書き換えていた、という事故がこれで起きます。
空にしておけば、`config.settings` を指定した時点で
必要な設定が1つも無いので即座に落ちます。

**問4.**
開発機の仮想環境に `requests` が別の経路で入っていて、
`requirements.txt` の間違いを覆い隠していたためです。
テストはその環境で動くので、宣言が間違っていても通ります。
気づくには、依存を空の環境で入れ直す必要があります。
`docker compose build --no-cache` がそのまま「空の環境の再現」になります。

**問5.**
監視の都合で本番の防御を下げることになるからです。
`SECURE_SSL_REDIRECT` を無効にすると、監視だけでなく
実際の利用者の HTTP アクセスもリダイレクトされなくなります。
正しいのは、監視側が nginx を通ってきたのと同じ形
（`Host` と `X-Forwarded-Proto`）でリクエストを送ることです。

</details>

---

## 12. Git の差分

```text
ブランチ: main
タグ　　: day-10
コミット: day-10: 設定を環境ごとに分け、本番構成をDockerで組む
```

前日からの差分を見る:

```bash
git diff day-09 day-10
```

10日目時点の状態で動かす:

```bash
git checkout day-10
```

主な変更:

```text
新規  config/settings/{__init__,local,test,production}.py
改名  config/settings.py -> config/settings/base.py
新規  Dockerfile, compose.yaml, compose.local-check.yaml
新規  docker/{entrypoint.sh,healthcheck.py}
新規  docker/nginx/{default.conf,local-check.conf}
新規  core/{views.py,checks.py}
新規  scripts/{backup.sh,restore_drill.sh}
新規  .env.example, .dockerignore
新規  config/tests/test_production_settings.py
新規  core/tests/{test_checks.py,test_healthz.py}
更新  manage.py, config/{wsgi,asgi,urls}.py, core/apps.py, requirements.txt
```

---

## 13. 次回予告

これで CMS 本体は完成です。第1部はここまでになります。

明日から第2部「10日で学ぶ Django 本番デプロイ」に入ります。
ここまでは全部、自分の開発機の中の話でした。
明日からは**インターネットに繋がったサーバー**を扱います。

第2部の初日は、Linux サーバーの初期設定です。

- 借りたばかりのサーバーに、最初に何をするか
- なぜ root で作業してはいけないのか
- 自動更新をどこまで有効にするか

「借りて 30 分放置しただけのサーバーに、
何回ログイン試行が来ているか」を実際に数えるところから始めます。

---

*この記事のコードは <https://github.com/kurumonn/DjangoCMS>（タグ `day-10`）にあります。*
