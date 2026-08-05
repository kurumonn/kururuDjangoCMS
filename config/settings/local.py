"""開発機用の設定。

    python manage.py runserver

manage.py はこれを既定にしている。

この設定は**本番では絶対に使わない**。SQLite・平文のシークレット・
メールをコンソールへ出す、など、開発の速さを優先した選択が入っている。
"""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, env_bool

DEBUG = True

# 開発用の固定キー。
# "django-insecure-" で始めているのは Django の慣習で、
# `manage.py check --deploy` がこの接頭辞を見て警告を出してくれるため。
SECRET_KEY = "django-insecure-dev-only-do-not-use-in-production"

# コメントの IP ハッシュ専用の鍵。SECRET_KEY とは別の値にする。
#
# 開発でも別の値にしておくのは、うっかり同じ値で書いたコードが
# 「動くから正しい」と見えてしまわないようにするため。
# 本番では production.py が環境変数を必須にしている。
COMMENT_IP_HASH_KEY = "django-insecure-dev-only-ip-hash-key"

ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]

# ---------------------------------------------------------------------------
# データベース
# ---------------------------------------------------------------------------
# 開発は SQLite のまま。1ファイルで済み、消してやり直すのも簡単。
#
# ただし本番は PostgreSQL なので、**SQLite でしか通らないコードを書ける**
# ことに注意する。実例:
#   * SQLite は型をほぼ検査しないが、PostgreSQL は厳密
#   * 一意制約の追加（AddField→AlterField）は PostgreSQL でだけ index 衝突する
#   * 文字列の並び順（大文字小文字の扱い）が違う
# 本番相当を確かめたいときは compose.yaml の db サービスを使う。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# ---------------------------------------------------------------------------
# キャッシュ
# ---------------------------------------------------------------------------
# 開発は1プロセスなので、ローカルメモリで足りる。
# 本番でこれを使うとレート制限がワーカー数だけ緩むので、production.py で Redis にする。
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "kururucms-local",
    }
}

# ---------------------------------------------------------------------------
# メール
# ---------------------------------------------------------------------------
# 送らずにコンソールへ出す。ワンタイムコードもここに出るので、
# 開発中はメールサーバーが無くてもログインを試せる。
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# ---------------------------------------------------------------------------
# パスキー（WebAuthn）
# ---------------------------------------------------------------------------
# WebAuthn は HTTPS でしか動かない（localhost だけ例外）。
# 開発中は緩める必要があるが、DEBUG と連動させてはいけない。
# DEBUG は環境変数の書き忘れで本番でも True になることがあり、
# そのとき一緒にこの保護まで外れてしまうため、独立した変数にしておく。
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = env_bool("DJANGO_MFA_ALLOW_INSECURE_ORIGIN", True)
