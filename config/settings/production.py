"""本番用の設定。

    DJANGO_SETTINGS_MODULE=config.settings.production

wsgi.py / asgi.py はこれを既定にしている。

この設定の方針は「足りないものは起動時に落とす」。
黙って安全でない既定値へ落ちる設計にすると、
「動いているから大丈夫」と思い込んだまま公開してしまう。
"""

import os
from urllib.parse import quote

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


def require_secret(name: str, minimum_length: int) -> str:
    value = require(name)
    if len(value) < minimum_length:
        raise RuntimeError(
            f"環境変数 {name} は {minimum_length} 文字以上のランダム値にしてください。"
        )
    return value


SECRET_KEY = require_secret("DJANGO_SECRET_KEY", 50)

# コメントの IP ハッシュ専用の鍵。SECRET_KEY とは分ける。
#
# SECRET_KEY は漏えいしたら必ず入れ替えるものだが、そのとき
# IP ハッシュまで一斉に変わると、連投検出やスパム対策の履歴が
# 過去と繋がらなくなる。鍵の寿命が違うものは鍵を分ける。
#
# 本番では必須にしている。未設定でも SECRET_KEY で動いてしまうと、
# 「分けたつもりで分かれていない」状態に気づけないため。
COMMENT_IP_HASH_KEY = require_secret("DJANGO_COMMENT_IP_HASH_KEY", 32)
if COMMENT_IP_HASH_KEY == SECRET_KEY:
    raise RuntimeError(
        "DJANGO_COMMENT_IP_HASH_KEY は DJANGO_SECRET_KEY と別の値にしてください。"
    )

# ALLOWED_HOSTS を空のままにすると、DEBUG=False では全リクエストが 400 になる。
# 「本番に上げたら真っ白」の原因で最も多いもののひとつ。
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")
if not ALLOWED_HOSTS:
    raise RuntimeError(
        "DJANGO_ALLOWED_HOSTS が未設定です。"
        " 例: DJANGO_ALLOWED_HOSTS=cms.example.com,www.example.com"
    )
if "*" in ALLOWED_HOSTS:
    raise RuntimeError(
        "DJANGO_ALLOWED_HOSTS にワイルドカード '*' は指定できません。"
    )

# CSRF の検証はスキーム込みで行う。ホスト名だけでは足りない。
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS") or [
    f"https://{host}" for host in ALLOWED_HOSTS
]

# ---------------------------------------------------------------------------
# データベース（PostgreSQL）
# ---------------------------------------------------------------------------
_database_role_prefix = (
    "POSTGRES_MIGRATION"
    if os.environ.get("APP_START_MODE") == "migrate"
    else "POSTGRES_APP"
)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": require("POSTGRES_DB"),
        "USER": require(f"{_database_role_prefix}_USER"),
        "PASSWORD": require(f"{_database_role_prefix}_PASSWORD"),
        "HOST": os.environ.get("POSTGRES_HOST", "db"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        # 接続を使い回す秒数。0 だとリクエストごとに接続し直す。
        "CONN_MAX_AGE": int(os.environ.get("POSTGRES_CONN_MAX_AGE", "60")),
        # 使い回す接続が生きているか、使う前に確かめる。
        # これが無いと、DB を再起動した直後の数リクエストが必ず失敗する。
        "CONN_HEALTH_CHECKS": True,
    }
}

# ---------------------------------------------------------------------------
# キャッシュ・セッション（Redis）
# ---------------------------------------------------------------------------
# Redis を共有キャッシュにする理由は速度ではなく、**正しさ**。
#
# allauth のレート制限は回数をキャッシュに記録する。
# ローカルメモリだとワーカーごとに別の数え方になるので、
# 「5回まで」がワーカー4本で実質20回になる。
# ログインの総当たり制限が4倍緩むということなので、これは性能の話ではない。
REDIS_PASSWORD = require_secret("REDIS_PASSWORD", 32)
REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
REDIS_DB = int(os.environ.get("REDIS_DB", "0"))
REDIS_URL = (
    f"redis://:{quote(REDIS_PASSWORD, safe='')}@{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
)

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": REDIS_URL,
    }
}

# セッションもキャッシュへ置く。DB より速く、複数ワーカーでも共有される。
# 保存先を Redis だけにすると再起動で全員ログアウトになるため、
# 読みはキャッシュ・書きは DB にも残す cached_db を使う。
SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"

# ---------------------------------------------------------------------------
# HTTPS
# ---------------------------------------------------------------------------
# Nginx が HTTPS を終端し、Django へは HTTP で渡す構成を前提にする。
# このヘッダーを見ないと、Django は常に「HTTP で来た」と判断し、
# SECURE_SSL_REDIRECT が無限リダイレクトを起こす。
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# HSTS（HTTP Strict Transport Security）。
# 「このドメインは今後 HTTPS でしか繋がない」とブラウザーに覚えさせる。
#
# ★取り消せない設定である★
# 一度送ると、ブラウザーは max-age の期間そのドメインを HTTPS 固定にする。
# 証明書が切れると「回避不能なエラー画面」になり、HTTP へ落として
# 復旧することもできない。だから段階的に上げる。
#
#   1. まず 3600（1時間）で出す
#   2. サイト全体が HTTPS で問題なく動くことを数日確認する
#   3. includeSubDomains を有効にする（サブドメインも全部 HTTPS になる）
#   4. 最後に 31536000（1年）へ上げ、必要なら preload を申請する
#
# 既定を1年にしていないのは、この手順を飛ばせないようにするため。
SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_SECURE_HSTS_SECONDS", "3600"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
SECURE_HSTS_PRELOAD = env_bool("DJANGO_SECURE_HSTS_PRELOAD", False)

# `manage.py check --deploy` は、上の2つが False だと警告を出す
# （security.W005 / W021）。しかしこの2つは取り消せない設定なので、
# 「警告が出ているから今すぐ True にする」は危険であり、
# 起動を止める（--fail-level WARNING）理由にもならない。
#
# そこで、まだその段階に来ていないあいだだけ黙らせる。
# True にすれば警告の対象から外れるので、この行も自然に効かなくなる。
#
# ただし黙らせっぱなしは「ただ忘れる」のと同じなので、
# 代わりに core/checks.py の check_hsts_rollout が、
# 起動のたびに「今どの段階で、次に何をするか」を出す。
SILENCED_SYSTEM_CHECKS = []
if not SECURE_HSTS_INCLUDE_SUBDOMAINS:
    SILENCED_SYSTEM_CHECKS.append("security.W005")
if not SECURE_HSTS_PRELOAD:
    SILENCED_SYSTEM_CHECKS.append("security.W021")

# Nginx の背後にいるので、X-Forwarded-For は1段ぶんだけ信用する。
try:
    TRUSTED_PROXY_COUNT = int(os.environ.get("DJANGO_TRUSTED_PROXY_COUNT", "1"))
except ValueError as exc:
    raise RuntimeError("DJANGO_TRUSTED_PROXY_COUNT は整数で指定してください。") from exc
if TRUSTED_PROXY_COUNT != 1:
    raise RuntimeError(
        "このCompose構成の DJANGO_TRUSTED_PROXY_COUNT は Nginx 1段に合わせて1です。"
    )
ALLAUTH_TRUSTED_PROXY_COUNT = TRUSTED_PROXY_COUNT

# 本番でパスキーの origin 検査を緩めることは無い。環境変数からも読まない。
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = False

# ---------------------------------------------------------------------------
# 静的ファイル
# ---------------------------------------------------------------------------
# 内容のハッシュをファイル名に含める。
# ブラウザーが古い CSS を掴んだままになる事故を防ぎ、
# 長いキャッシュ期間を安全に設定できる。
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage"
    },
}

# ---------------------------------------------------------------------------
# メール
# ---------------------------------------------------------------------------
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = require("DJANGO_EMAIL_HOST")
EMAIL_PORT = int(os.environ.get("DJANGO_EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("DJANGO_EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("DJANGO_EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", True)
EMAIL_TIMEOUT = 10  # 秒。無いと SMTP が応答しないときリクエストが固まる。

# ---------------------------------------------------------------------------
# ログ
# ---------------------------------------------------------------------------
# 標準出力へ出す。ファイルへ書かないのは、コンテナのログを
# docker compose logs や journald 側で一元的に扱うため。
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        # 500 の詳細。DEBUG=False では画面に出ないので、ここで拾わないと
        # 「エラーページだけ見えて原因が分からない」状態になる。
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        # SQL は出さない。出すとパスワードリセットのトークンなどが
        # ログに残る場合がある。
        "django.db.backends": {"level": "WARNING"},
    },
}
