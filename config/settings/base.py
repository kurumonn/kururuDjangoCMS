"""どの環境でも同じ設定。

環境ごとに変わるもの（DEBUG・データベース・キャッシュ・メール送信・HTTPS）は
ここには書かない。local.py / test.py / production.py が受け持つ。

判断の基準は「開発機と本番で値が違ってよいか」。
違ってよいものを base に書くと、本番だけ設定が抜けている状態に気づけなくなる。
"""

import os
from pathlib import Path

# BASE_DIR は manage.py があるディレクトリ。
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


# 管理画面のURLパス。既定の "admin" のままだと総当たり攻撃の的になるため、
# 本番では環境変数で推測しにくい値へ変更する。
# ただしこれは「発見されにくくする」対策であって、認証の代わりにはならない。
# 実際の防御は 9日目の MFA 必須化とレート制限で行う。
ADMIN_URL_PATH = os.environ.get("DJANGO_ADMIN_URL_PATH", "admin").strip("/")

# ---------------------------------------------------------------------------
# アプリケーション
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sitemaps",
    # allauth の「パスキー一覧」「ログイン中の端末」テンプレートが
    # naturaltime フィルタを使う。入れ忘れると、その画面だけ
    # KeyError: 'humanize' で落ちる。
    "django.contrib.humanize",
    # django-allauth（8日目）
    # sites は socialaccount が使う。SITE_ID と合わせて必要。
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    # ログイン中のセッション一覧と、他端末からのログアウト
    "allauth.usersessions",
    # 多要素認証（9日目）: TOTP / リカバリコード / パスキー
    "allauth.mfa",
    # 自作アプリ
    "core",
    "accounts",
    "blog",
    "pages",
    "media_library",
    "comments",
    "seo",
    "dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # allauth が必須とするミドルウェア。
    # 入れ忘れると、ログイン処理の途中で必ず例外になる。
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
    # 管理者に多要素認証を必須にする（9日目）。
    # allauth のミドルウェアより後に置く。
    # 先に置くと、allauth がセッションを整える前に判定してしまう。
    "accounts.middleware.StaffMfaRequiredMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "seo.context_processors.site_settings",
                "seo.context_processors.sidebar",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------
# ★最重要★ カスタムユーザーモデルは「最初の migrate より前」に指定する。
# 後から差し替えると、記事の著者・コメント・権限の外部キーをすべて作り直すことになる。
AUTH_USER_MODEL = "accounts.User"

# Argon2 を第一候補にする。Django 標準の PBKDF2 より攻撃コストが高い。
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 12},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    # 管理画面用。ユーザー名とパスワードで認証する。
    "django.contrib.auth.backends.ModelBackend",
    # allauth 用。メールアドレスやソーシャルログインを扱う。
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_URL = "account_login"
LOGIN_REDIRECT_URL = "/dashboard/"
LOGOUT_REDIRECT_URL = "/"

# ---------------------------------------------------------------------------
# django-allauth（8日目）
# ---------------------------------------------------------------------------
# ログインはメールアドレスで行う。
# ユーザー名は表示用に残すが、ログインには使わない。
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True
# `mandatory` email verification is invalid unless the email field is
# explicitly required.  allauth's system check treats the two settings as a
# pair; leaving this out prevents every management command and test run from
# starting at all.
ACCOUNT_EMAIL_REQUIRED = True

# メール確認を必須にする。
# "optional" にすると、他人のメールアドレスで登録して
# そのアドレス宛の通知を受け取れてしまう。
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_MAX_ATTEMPTS = 3
ACCOUNT_CONFIRM_EMAIL_ON_GET = False  # GET で確認を完了させない（メールの先読み対策）

# --- メールワンタイムコードでのログイン ---
# パスワードを覚えていなくても、メールに届く数字でログインできる。
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 180          # 有効期限（秒）
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3       # 入力の試行回数
ACCOUNT_LOGIN_BY_CODE_MAX_RESEND_COUNT = 3   # 再送の上限

# コードの形式。allauth の既定は "BCDF-GHJK" のような英字8桁。
# ここでは数字6桁にする。スマートフォンで入力しやすく、
# メールからの読み取りミスも減るため。
#
# 数字6桁は 100 万通りしかないので、単体では弱い。
# 次の3つを組み合わせて初めて実用に耐える。
#   1. 有効期限 180 秒（上の TIMEOUT）
#   2. 試行3回で無効化（上の MAX_ATTEMPTS）
#   3. 発行そのものを 5分に3回まで（下の RATE_LIMITS）
# どれか1つでも外すと総当たりが成立するので、一緒に扱う。
ACCOUNT_LOGIN_BY_CODE_FORMAT = {"numeric": True, "length": 6, "dashed": False}
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_FORMAT = {
    "numeric": True,
    "length": 6,
    "dashed": False,
}

# アカウントの存在を漏らさない。
# 「このメールアドレスは登録されていません」と返すと、
# 総当たりで会員かどうかを調べられる。
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False  # 未登録アドレスへ「登録がありません」メールを送らない

# パスワード変更後は他端末のセッションを切る。
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
# ログアウトは POST のみ（GET を許すと強制ログアウトさせられる）。
ACCOUNT_LOGOUT_ON_GET = False

# 重要な操作の前に、もう一度本人確認を求める。
ACCOUNT_REAUTHENTICATION_REQUIRED = True
ACCOUNT_REAUTHENTICATION_TIMEOUT = 300  # 秒

ACCOUNT_SESSION_REMEMBER = None  # 「ログイン状態を保持する」を利用者に選ばせる
ACCOUNT_EMAIL_SUBJECT_PREFIX = "[KururuCMS] "

# --- レート制限 ---
# 総当たりとメール爆撃を止める。単位は "回/期間"。
#
# ★ここが 10日目で効いてくる。★
# レート制限の回数は CACHES に記録される。
# ローカルメモリキャッシュはプロセスごとに別物なので、
# Gunicorn をワーカー4本で動かすと、制限が実質4倍に緩む。
# 「5回まで」のつもりが20回試せる。
# 本番では必ず共有キャッシュ（Redis）にする。production.py を参照。
ACCOUNT_RATE_LIMITS = {
    "login": "5/5m",                  # IP ごとのログイン試行
    "login_failed": "5/5m/ip,3/5m/key",  # 失敗回数（アカウント単位も含む）
    "signup": "5/h/ip",
    "send_email": "10/h",
    "change_email": "3/h",
    "manage_email": "10/m/user",
    "reset_password": "5/h/ip,3/h/key",
    "reset_password_from_key": "10/m/ip",
    "confirm_email": "5/m/key",
    "request_login_code": "3/5m/key",  # ワンタイムコードの発行
}

# セッション一覧（どの端末からログインしているか）
USERSESSIONS_TRACK_ACTIVITY = True

# ソーシャルログイン。
# 認証情報は環境変数から読む。settings.py に直接書かない。
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    },
    "github": {
        "APP": {
            "client_id": os.environ.get("GITHUB_CLIENT_ID", ""),
            "secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["user:email"],
    },
}

# ---------------------------------------------------------------------------
# 多要素認証・パスキー（9日目）
# ---------------------------------------------------------------------------
# 3種類を有効にする。用途が違うので、どれか1つでは足りない。
#
#   totp           … スマートフォンの認証アプリ。端末を持っていれば使える。
#   recovery_codes … 認証アプリを失ったときの最後の手段。紙に印刷して保管する。
#   webauthn       … パスキー。端末の生体認証や物理キー。フィッシングに強い。
#
# recovery_codes を外すと、スマートフォンを失くした利用者が
# 二度とログインできなくなる。必ず入れる。
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]

# パスキーだけでログインできるようにする（パスワード入力なし）。
MFA_PASSKEY_LOGIN_ENABLED = True
# 登録時のパスキー作成は無効のまま。
# 最初からパスキーだけで作らせると、その端末を失った時点で復旧手段が無くなる。
MFA_PASSKEY_SIGNUP_ENABLED = False

MFA_TOTP_ISSUER = os.environ.get("DJANGO_MFA_ISSUER", "KururuCMS")
MFA_TOTP_PERIOD = 30
MFA_TOTP_DIGITS = 6
# 時計のずれを吸収する幅（秒）。広げすぎると総当たりが楽になる。
MFA_TOTP_TOLERANCE = 30

MFA_RECOVERY_CODE_COUNT = 10
MFA_RECOVERY_CODE_DIGITS = 8
# リカバリコードは発行時に一度だけ見せる。
# 後からいつでも見られる状態にすると、画面を覗かれただけで突破される。
MFA_RECOVERY_CODES_SHOW_ONCE = True

# パスキーやTOTPの削除など、重要な操作の前に再認証を求める。
MFA_ALLOW_UNVERIFIED_EMAIL = False

# 管理画面へ入れる利用者には多要素認証を必須にする。
# 記事を書くだけの利用者にまで強制すると運用が回らないため、対象を絞る。
MFA_REQUIRED_FOR_STAFF = env_bool("DJANGO_MFA_REQUIRED_FOR_STAFF", True)

# ソーシャル側で確認済みのメールアドレスは、こちらで再確認しない。
SOCIALACCOUNT_EMAIL_AUTHENTICATION = False
# 既存アカウントへ自動で紐づけない。
# 自動で繋ぐと、攻撃者が同じメールアドレスのソーシャルアカウントを用意するだけで
# 既存アカウントを乗っ取れる可能性がある。
SOCIALACCOUNT_AUTO_SIGNUP = False

# ---------------------------------------------------------------------------
# 国際化
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "ja"
TIME_ZONE = "Asia/Tokyo"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# 静的ファイル・メディア
# ---------------------------------------------------------------------------
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# アップロードの上限。Django が受け取る段階で切るための保険。
# 実際の検証は media_library/validators.py が行う。
# Nginx 側でも client_max_body_size を設定する（デプロイ編6日目）。
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024   # 6 MiB
FILE_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
# フォームの項目数の上限。極端に多い項目を送りつけるDoSを防ぐ。
DATA_UPLOAD_MAX_NUMBER_FIELDS = 500

# 信頼できるリバースプロキシの段数。
# 0 のとき X-Forwarded-For を一切信用しない（開発既定）。
# Nginx の背後に置いたら 1 にする。
TRUSTED_PROXY_COUNT = int(os.environ.get("DJANGO_TRUSTED_PROXY_COUNT", "0"))

# ---------------------------------------------------------------------------
# セキュリティ既定値
# ---------------------------------------------------------------------------
# ここには「どの環境でも下げてはいけない」ものだけを置く。
# HTTPS を前提とする設定（Secure Cookie・HSTS・SSLリダイレクト）は
# production.py にある。開発機は HTTP なので、ここに書くと開発が止まる。
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

DEFAULT_FROM_EMAIL = os.environ.get("DJANGO_DEFAULT_FROM_EMAIL", "noreply@example.com")
