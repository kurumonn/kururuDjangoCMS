"""運用まわりのシステムチェック（10日目）。

accounts/checks.py と同じ方針。
DEBUG=True のあいだは黙り、本番相当のときだけ声を上げる。
"""

from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, Info, Warning, register

# HSTS を上げていく手順。
# 「いつか上げる」ではなく、次にやることを毎回起動時に見せる。
HSTS_STEPS = [
    (3600, "1時間。まず HTTPS が全ページで問題なく動くことを確かめる段階。"),
    (604800, "1週間。数日間そのまま運用して問題が出ないことを確かめる段階。"),
    (31536000, "1年。ここまで来たら includeSubDomains と preload を検討できる。"),
]


@register(deploy=True)
def check_hsts_rollout(app_configs, **kwargs):
    """HSTS の設定が、段階のどこにいるかを知らせる。

    Django 標準の `manage.py check --deploy` は、
    SECURE_HSTS_INCLUDE_SUBDOMAINS と SECURE_HSTS_PRELOAD が False だと
    警告（security.W005 / W021）を出す。

    しかしこの2つは**取り消せない**設定である。
    一度ブラウザーへ送ると、max-age の期間そのドメインは HTTPS 固定になり、
    証明書が切れたときに HTTP へ戻して復旧することができない。
    サブドメインまで巻き込む includeSubDomains なら影響はもっと広い。

    だから「警告が出ているから今すぐ True にする」は危険で、
    正しいのは「順番に上げる」。
    production.py はその2件の警告を意図的に黙らせ、
    代わりにこのチェックが**今どの段階にいて次に何をするか**を出す。

    黙らせた警告の代わりを用意せずに黙らせると、
    ただ忘れるだけになるので、必ず対にする。
    """
    if settings.DEBUG:
        return []

    seconds = getattr(settings, "SECURE_HSTS_SECONDS", 0)

    if seconds <= 0:
        return [
            Error(
                "SECURE_HSTS_SECONDS が 0 です。HSTS が無効になっています。",
                hint=(
                    "HTTPS で公開するなら、まず DJANGO_SECURE_HSTS_SECONDS=3600 から"
                    "始めてください。無効のままだと、初回アクセスが HTTP へ"
                    "誘導される攻撃を防げません。"
                ),
                id="core.E001",
            )
        ]

    include_subdomains = getattr(settings, "SECURE_HSTS_INCLUDE_SUBDOMAINS", False)
    preload = getattr(settings, "SECURE_HSTS_PRELOAD", False)

    # 今どの段階か。
    step = 0
    for index, (threshold, _) in enumerate(HSTS_STEPS):
        if seconds >= threshold:
            step = index

    current = HSTS_STEPS[step]
    if seconds < HSTS_STEPS[-1][0]:
        next_seconds = HSTS_STEPS[step + 1][0] if seconds >= current[0] else current[0]
        next_action = f"問題が無ければ DJANGO_SECURE_HSTS_SECONDS={next_seconds} へ上げる"
    elif not include_subdomains:
        next_action = (
            "サブドメインをすべて HTTPS で出せるなら"
            " DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS=1 にする"
        )
    elif not preload:
        next_action = (
            "hstspreload.org への登録を検討する"
            "（DJANGO_SECURE_HSTS_PRELOAD=1。登録すると取り消しに数か月かかる）"
        )
    else:
        next_action = "完了。これ以上上げる段階はない。"

    return [
        Info(
            f"HSTS: max-age={seconds} 秒 / includeSubDomains={include_subdomains}"
            f" / preload={preload}",
            hint=f"{current[1]} 次にやること: {next_action}",
            id="core.I001",
        )
    ]


@register(deploy=True)
def check_proxy_configuration(app_configs, **kwargs):
    """リバースプロキシまわりの設定が噛み合っているか。

    ここが食い違うと、症状が「無限リダイレクト」や
    「レート制限が効かない」という形で出る。
    設定そのものを見ないと原因に辿り着けないので、起動時に検査する。
    """
    if settings.DEBUG:
        return []

    issues = []

    ssl_redirect = getattr(settings, "SECURE_SSL_REDIRECT", False)
    proxy_header = getattr(settings, "SECURE_PROXY_SSL_HEADER", None)

    if ssl_redirect and not proxy_header:
        issues.append(
            Error(
                "SECURE_SSL_REDIRECT が有効ですが、SECURE_PROXY_SSL_HEADER が未設定です。",
                hint=(
                    "Nginx が HTTPS を終端して Django へ HTTP で渡す構成では、"
                    "Django は常に「HTTP で来た」と判断します。"
                    "そのため HTTPS へリダイレクトし続け、無限ループになります。"
                    'SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") '
                    "を設定し、Nginx 側でも proxy_set_header X-Forwarded-Proto $scheme; "
                    "を送ってください。"
                ),
                id="core.E002",
            )
        )

    proxy_count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    allauth_proxy_count = getattr(settings, "ALLAUTH_TRUSTED_PROXY_COUNT", 0)
    if allauth_proxy_count != proxy_count:
        issues.append(
            Error(
                "TRUSTED_PROXY_COUNT と ALLAUTH_TRUSTED_PROXY_COUNT が一致しません。",
                hint=(
                    "コメント/IP記録と認証レート制限が同じクライアントIPを使うよう、"
                    "実際の信頼できるプロキシ段数を両方へ設定してください。"
                ),
                id="core.E003",
            )
        )
    if proxy_header and proxy_count == 0:
        issues.append(
            Warning(
                "リバースプロキシの背後にいるのに TRUSTED_PROXY_COUNT が 0 です。",
                hint=(
                    "0 のとき X-Forwarded-For を信用しないため、"
                    "すべてのアクセスがプロキシの IP に見えます。"
                    "レート制限とコメントのIP記録が全員まとめて1つに数えられ、"
                    "実質機能しません。Nginx が1段なら 1 にしてください。"
                ),
                id="core.W001",
            )
        )

    if proxy_count > 3:
        issues.append(
            Warning(
                f"TRUSTED_PROXY_COUNT が {proxy_count} と大きすぎます。",
                hint=(
                    "この数だけ X-Forwarded-For の末尾から遡って信用します。"
                    "実際の段数より多いと、利用者が自分で付けた偽の IP を"
                    "信じてしまい、レート制限を回避されます。"
                ),
                id="core.W002",
            )
        )

    return issues
