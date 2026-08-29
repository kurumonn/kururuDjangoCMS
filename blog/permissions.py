"""記事操作の認可境界。

所有者かどうかだけでは、権限剥奪後も記事を変更できてしまう。
すべての変更入口で、Django の action permission と対象記事の範囲を
組み合わせて判定する。
"""

from __future__ import annotations

from .models import Article


def can_change_article(user, article: Article) -> bool:
    """変更権限を持ち、かつ対象記事が本人の範囲またはレビュー範囲か。"""
    if not user.is_authenticated or not user.has_perm("blog.change_article"):
        return False
    return article.author_id == user.pk or user.has_perm("blog.review_article")


def can_edit(user, article: Article) -> bool:
    """現在の公開状態を含めて記事を編集できる利用者か。"""
    if not can_change_article(user, article):
        return False
    if (
        article.status == Article.Status.PUBLISHED
        and not user.has_perm("blog.publish_article")
    ):
        return False
    return True


def can_review(user) -> bool:
    return user.is_authenticated and user.has_perm("blog.review_article")


def can_publish(user) -> bool:
    return user.is_authenticated and user.has_perm("blog.publish_article")
