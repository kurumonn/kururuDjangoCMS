"""KururuCMS のルート URLconf。

リクエストは必ずここを最初に通り、上から順にパターンが照合される。
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from core.views import healthz

urlpatterns = [
    # 死活監視。compose の healthcheck と、デプロイ編の監視から叩かれる。
    # 認証を掛けていないのは、監視側に認証情報を持たせたくないため。
    # 返すのは状態だけで、内部の情報は含めない（core/views.py を参照）。
    path("healthz/", healthz, name="healthz"),
    # ★管理画面のログイン画面を、allauth のログインへ差し替える★
    #
    # `admin.site.urls` には admin 自身のログイン画面が含まれている。
    # これは allauth のログインフローとは**別物**で、
    # ログインステージ（＝2段目の認証）を一切通らない。
    #
    # 開けたままにしていると、TOTP もパスキーも登録済みの管理者が
    # パスワードだけで管理画面へ入れる。多要素認証を必須にした意味が無くなる。
    # 認証バックエンドは allauth のものが効いているので、
    # メールアドレスでも通ってしまう。
    #
    # URL は上から順に照合されるので、`admin.site.urls` より**前**に置く。
    # 後ろに置くと admin 側が先に一致して、この行は一生使われない。
    #
    # `query_string=True` で `?next=...` を引き継ぐ。
    # 落とすと、ログイン後に目的のページではなくトップへ戻される。
    path(
        f"{settings.ADMIN_URL_PATH}/login/",
        RedirectView.as_view(pattern_name="account_login", query_string=True),
        name="admin_login_redirect",
    ),
    # 管理画面のパスは環境変数で変更できるようにしておく。
    # 既定の /admin/ は総当たり攻撃の標的になりやすい。
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
    # 認証は django-allauth に任せる（8日目）。
    #   /accounts/login/            ログイン（メールアドレス＋パスワード）
    #   /accounts/login/code/       メールワンタイムコードでログイン
    #   /accounts/signup/           ユーザー登録
    #   /accounts/email/            メールアドレスの管理
    #   /accounts/password/change/  パスワード変更
    #   /accounts/3rdparty/         ソーシャルアカウント連携
    #   /accounts/sessions/         ログイン中の端末一覧
    #
    # allauth.urls は、INSTALLED_APPS の中身を見て
    # socialaccount / mfa / usersessions の URL を自動で足す。
    # それぞれを個別に include してはいけない。
    # 同じ URL 名が二重に登録され、reverse() が後から登録した方を返すため、
    # リンク先が意図しないパスになる。
    path("accounts/", include("allauth.urls")),
    path("dashboard/", include("dashboard.urls")),
    path("pages/", include("pages.urls")),
    path("", include("cms_plugins.urls")),
    # sitemap.xml / feed / robots.txt はサイト直下に置く。
    path("", include("seo.urls")),
    path("", include("comments.urls")),
    path("", include("blog.urls")),
]

if settings.DEBUG:
    # 開発時のみ Django がメディアファイルを配信する。
    # 本番では Nginx が配信する（デプロイ編6日目）。
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
