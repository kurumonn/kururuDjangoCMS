"""XML サイトマップ。

Django 標準の django.contrib.sitemaps を使う。自作しないのは、
lastmod の書式・分割・件数上限といった細部を仕様どおりに扱うのが面倒なため。

重要なのは「公開してよいものだけを載せる」こと。
サイトマップに未公開記事の URL を書いてしまうと、
検索エンジンへ下書きの存在と URL を自分から教えることになる。
"""

from urllib.parse import urlsplit

from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from blog.models import Article, Category, Tag
from pages.models import Page

from .models import SiteSetting


class ConfiguredDomainSitemap(Sitemap):
    """サイトマップの絶対URLを SiteSetting.base_url から作る。

    Django のサイトマップは、既定では
    django.contrib.sites か「リクエストのホスト名」からドメインを決める。
    しかしこの CMS では、canonical URL・OGP・robots.txt・JSON-LD が
    すべて SiteSetting.base_url を使っている。

    両者を放置すると、次のような食い違いが起きる。

        canonical : https://cms.example.com/articles/hello/
        sitemap   : https://cms.internal.local/articles/hello/

    リバースプロキシの背後や、内部ホスト名でアクセスされたときに実際に起きる。
    検索エンジンから見ると「サイトマップに載っている URL と、
    そのページが名乗る正規 URL が違う」状態になり、
    どちらを登録すべきか判断できなくなる。

    そこで、絶対URLの出所を SiteSetting へ一本化する。

    ``get_urls()`` を丸ごと差し替えるのではなく、Django が用意している
    ``get_domain()`` / ``get_protocol()`` だけを上書きする。
    ページ分割や lastmod の扱いは Django 側の実装をそのまま使える。
    """

    def _parts(self):
        return urlsplit(SiteSetting.load().base_url)

    def get_domain(self, site=None):
        netloc = self._parts().netloc
        if netloc:
            return netloc
        return super().get_domain(site)

    def get_protocol(self, protocol=None):
        scheme = self._parts().scheme
        if scheme:
            return scheme
        return super().get_protocol(protocol)


class ArticleSitemap(ConfiguredDomainSitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        # published() を通す。ここを .all() にすると下書きが漏れる。
        # noindex を指定した記事も載せない（載せたうえで noindex は矛盾する）。
        return Article.objects.published().filter(noindex=False, canonical_url="")

    def lastmod(self, obj: Article):
        return obj.updated_at

    def location(self, obj: Article) -> str:
        return obj.get_absolute_url()


class PageSitemap(ConfiguredDomainSitemap):
    changefreq = "monthly"
    priority = 0.5

    def items(self):
        return Page.objects.published().filter(noindex=False, canonical_url="")

    def lastmod(self, obj: Page):
        return obj.updated_at


class CategorySitemap(ConfiguredDomainSitemap):
    changefreq = "weekly"
    priority = 0.4

    def items(self):
        # 公開記事が1件も無いカテゴリは載せない（中身の無いページを増やさない）。
        return [c for c in Category.objects.all() if c.articles.published().exists()]

    def location(self, obj: Category) -> str:
        return obj.get_absolute_url()


class TagSitemap(CategorySitemap):
    priority = 0.3

    def items(self):
        return [t for t in Tag.objects.all() if t.articles.published().exists()]


class StaticViewSitemap(ConfiguredDomainSitemap):
    changefreq = "daily"
    priority = 1.0

    def items(self):
        return ["blog:article_list"]

    def location(self, item: str) -> str:
        return reverse(item)


SITEMAPS = {
    "articles": ArticleSitemap,
    "pages": PageSitemap,
    "categories": CategorySitemap,
    "tags": TagSitemap,
    "static": StaticViewSitemap,
}
