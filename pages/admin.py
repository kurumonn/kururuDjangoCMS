from django.contrib import admin

from .models import Page


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "published_at", "show_in_footer", "menu_order")
    list_filter = ("status", "show_in_footer")
    search_fields = ("title", "body")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "published_at"
    fieldsets = (
        (None, {"fields": ("title", "slug", "body")}),
        ("公開", {"fields": ("status", "published_at", "show_in_footer", "menu_order")}),
        (
            "SEO",
            {
                "classes": ("collapse",),
                "fields": (
                    "seo_title",
                    "seo_description",
                    "canonical_url",
                    "og_image",
                    "noindex",
                ),
            },
        ),
    )
