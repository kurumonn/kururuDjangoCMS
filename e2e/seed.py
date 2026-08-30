"""Seed only the records needed by the Docker E2E journey."""

import os
import uuid
from datetime import timedelta

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.utils import timezone

from blog.models import Article, Category
from cms_plugins.models import PluginActivation
from contact_forms.models import (
    ContactField,
    ContactForm,
    ContactPluginSetting,
    ContactSubmission,
)

viewer_password = os.environ["KURURU_E2E_VIEWER_PASSWORD"]
User = get_user_model()
viewer, _ = User.objects.update_or_create(
    username="e2e-viewer",
    defaults={
        "email": "viewer@example.test",
        "display_name": "E2E閲覧担当",
        "is_staff": True,
        "is_active": True,
        "is_superuser": False,
    },
)
viewer.set_password(viewer_password)
viewer.save(update_fields=["password"])
viewer.user_permissions.set(
    Permission.objects.filter(
        content_type__app_label="contact_forms",
        codename__in=("view_contactform", "view_contactfield"),
    )
)
EmailAddress.objects.update_or_create(
    user=viewer,
    email=viewer.email,
    defaults={"verified": True, "primary": True},
)

settings = ContactPluginSetting.load()
settings.default_retention_days = 7
settings.minimum_fill_seconds = 2
settings.save(update_fields=["default_retention_days", "minimum_fill_seconds"])

contact_form, _ = ContactForm.objects.update_or_create(
    slug="e2e-contact",
    defaults={
        "name": "E2Eお問い合わせ",
        "recipient_email": "owner@example.test",
        "subject": "E2E管理者通知",
        "autoresponder_subject": "E2E自動返信",
        "autoresponder_body": "お問い合わせを受け付けました。",
        "retention_days": 7,
        "is_active": False,
        "is_archived": False,
    },
)
contact_form.fields.all().delete()
ContactField.objects.create(
    form=contact_form,
    key="name",
    label="お名前",
    kind=ContactField.Kind.TEXT,
    required=True,
    order=0,
)
ContactField.objects.create(
    form=contact_form,
    key="email",
    label="メールアドレス",
    kind=ContactField.Kind.EMAIL,
    required=True,
    order=1,
)
ContactField.objects.create(
    form=contact_form,
    key="message",
    label="お問い合わせ内容",
    kind=ContactField.Kind.TEXTAREA,
    required=True,
    max_length=2_000,
    order=2,
)
contact_form.is_active = True
contact_form.save(update_fields=["is_active"])

PluginActivation.objects.update_or_create(
    key="kururu_forms", defaults={"enabled": True}
)
category, _ = Category.objects.get_or_create(
    slug="e2e", defaults={"name": "E2E"}
)
Article.objects.update_or_create(
    slug="e2e-contact-form",
    defaults={
        "title": "E2E問い合わせフォーム",
        "author": viewer,
        "category": category,
        "status": Article.Status.PUBLISHED,
        "published_at": timezone.now() - timedelta(minutes=1),
        "blocks": [
            {
                "type": "kururu_forms.contact_form",
                "data": {"form_id": contact_form.pk},
            }
        ],
        "noindex": True,
    },
)

ContactSubmission.objects.filter(
    form=contact_form, page_path="/e2e-expired/"
).delete()
expired = ContactSubmission.objects.create(
    form=contact_form,
    idempotency_key=uuid.uuid4(),
    payload={"kind": "retention-fixture"},
    status=ContactSubmission.Status.DELIVERED,
    ip_hash="0" * 64,
    page_path="/e2e-expired/",
)
ContactSubmission.objects.filter(pk=expired.pk).update(
    submitted_at=timezone.now() - timedelta(days=8)
)
print("e2e_seed=ok")
