"""テスト用のオブジェクト生成ヘルパー。

外部ライブラリを増やさず、素の ORM だけで書く。
"""

from django.contrib.auth import get_user_model
from django.utils import timezone

from blog.models import Article, Category, Tag

User = get_user_model()


def create_user(username="author1", **kwargs):
    defaults = {
        "email": f"{username}@example.com",
        "password": "test-pass-phrase-1234",
    }
    defaults.update(kwargs)
    password = defaults.pop("password")
    user = User(username=username, **defaults)
    user.set_password(password)
    user.save()
    return user


def grant(user, *codenames):
    """``app_label.codename`` 形式で権限を付与する。

    テストの中で「権限を持つ人／持たない人」を明示的に作り分けるために使う。
    """
    from django.contrib.auth.models import Permission

    for dotted in codenames:
        app_label, codename = dotted.split(".", 1)
        user.user_permissions.add(
            Permission.objects.get(
                content_type__app_label=app_label, codename=codename
            )
        )
    # 権限キャッシュを捨てて、追加した権限を即座に反映させる。
    if hasattr(user, "_perm_cache"):
        del user._perm_cache
    return User.objects.get(pk=user.pk)


def create_author(username="writer", **kwargs):
    """記事を投稿・編集・削除できる一般ユーザー。"""
    user = create_user(username=username, **kwargs)
    user = grant(
        user, "blog.add_article", "blog.change_article", "blog.delete_article"
    )
    add_totp(user)
    verify_email(user)
    return user


def create_staff(username="editor", **kwargs):
    """スタッフ（他人の記事も編集できる）。

    9日目に「管理者は多要素認証が必須」というミドルウェアを入れた。
    実運用では is_staff の利用者が認証手段を登録するまで他の画面へ進めない。
    テスト用のスタッフも同じ状態にそろえないと、
    「テストでは動くのに実際には設定画面へ飛ばされる」という食い違いが起きる。
    """
    user = create_user(username=username, is_staff=True, **kwargs)
    user = grant(
        user, "blog.add_article", "blog.change_article", "blog.delete_article"
    )
    add_totp(user)
    verify_email(user)
    return user


def verify_email(user):
    """メールアドレスを確認済みにする。

    `ACCOUNT_EMAIL_VERIFICATION = "mandatory"` なので、
    確認済みの `EmailAddress` が無いとログイン画面を通過できない。
    `Client.login()` はそこを見ないので、近道でログインしている限り
    足りないことに気づけない。
    """
    from allauth.account.models import EmailAddress

    EmailAddress.objects.get_or_create(
        user=user,
        email=user.email,
        defaults={"verified": True, "primary": True},
    )


def add_totp(user):
    """TOTP を登録済みにする（多要素認証の設定を済ませた状態）。"""
    from allauth.mfa.totp.internal import auth as totp_auth

    secret = totp_auth.generate_totp_secret()
    return totp_auth.TOTP.activate(user, secret).instance


def login_staff(client, user, password="test-pass-phrase-1234"):
    """スタッフとして、本番と同じ経路でログインする。

    `client.login()` を使ってはいけない。あれは
    `django.contrib.auth.login()` を直接呼ぶので allauth のログインフローを
    通らず、セッションに認証記録が残らない。

    `StaffMfaRequiredMiddleware` はその記録で「いくつの要素で成立した
    セッションか」を数える。記録の無いセッションは管理者向けの画面へ
    進めないので、近道でログインしたテストだけが落ちる。
    """
    from accounts.testing import login_through_allauth
    from django.core.cache import cache

    # allauth のIP/キー単位レート制限を別テストから持ち越さない。
    client.logout()
    cache.clear()
    login_through_allauth(client, user, password)


def create_editor(username="reviewer", **kwargs):
    """編集者（承認・公開・コメント管理ができる）。

    setup_groups コマンドの「編集者」ロールと同じ権限をそろえる。
    ここがずれていると、テストは通るのに実運用のグループでは
    権限が足りない、という食い違いが起きる。
    """
    user = create_user(username=username, **kwargs)
    user = grant(
        user,
        "blog.add_article",
        "blog.change_article",
        "blog.delete_article",
        "blog.publish_article",
        "blog.review_article",
        "comments.change_comment",
        "comments.delete_comment",
        "comments.view_comment",
    )
    add_totp(user)
    verify_email(user)
    return user


def create_category(name="お知らせ", **kwargs):
    return Category.objects.create(name=name, **kwargs)


def create_tag(name="django", **kwargs):
    return Tag.objects.create(name=name, **kwargs)


def create_article(
    title="テスト記事",
    *,
    author=None,
    category=None,
    status=Article.Status.PUBLISHED,
    published_at="now",
    body="本文です。",
    **kwargs,
):
    """記事を1件作る。

    published_at に "now" を渡すと現在時刻、None を渡すとそのまま未設定になる。
    """
    if author is None:
        author = create_user(username=f"author-{Article.objects.count() + 1}")
    if category is None:
        category = Category.objects.first() or create_category()
    if published_at == "now":
        published_at = timezone.now()

    return Article.objects.create(
        title=title,
        author=author,
        category=category,
        status=status,
        published_at=published_at,
        body=body,
        **kwargs,
    )
