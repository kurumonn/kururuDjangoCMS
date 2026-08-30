"""Assert database boundaries that are intentionally not exposed over HTTP."""

from __future__ import annotations

import os
from collections import Counter

from django.core.exceptions import ValidationError

from contact_forms.models import (
    ContactForm,
    ContactMaintenanceRun,
    ContactSubmission,
    MailDelivery,
)

phase = os.environ["E2E_ASSERT_PHASE"]
contact_form = ContactForm.objects.get(slug="e2e-contact")
public = ContactSubmission.objects.filter(
    form=contact_form,
    page_path="/articles/e2e-contact-form/",
)

if phase == "enqueued":
    assert contact_form.is_active and not contact_form.is_archived
    assert public.count() == 1
    submission = public.get()
    assert list(
        submission.deliveries.values_list("kind", "status")
    ) == [(MailDelivery.Kind.NOTIFICATION, MailDelivery.Status.PENDING)]
    empty = ContactForm(
        name="E2E空フォーム",
        slug="e2e-empty-form",
        recipient_email="owner@example.test",
        is_active=True,
    )
    try:
        empty.save()
    except ValidationError:
        pass
    else:
        raise AssertionError("zero-field active form was accepted")
    print("e2e_state=enqueued submissions=1 notification=pending autoreply=0")
elif phase == "delivered":
    assert public.count() == 1
    submission = public.get()
    deliveries = list(
        submission.deliveries.values_list("kind", "status", "attempts")
    )
    counts = Counter((kind, status) for kind, status, _ in deliveries)
    assert counts == Counter(
        {
            (MailDelivery.Kind.NOTIFICATION, MailDelivery.Status.SENT): 1,
            (MailDelivery.Kind.AUTOREPLY, MailDelivery.Status.SENT): 1,
        }
    )
    assert all(attempts == 1 for _, _, attempts in deliveries)
    assert submission.status == ContactSubmission.Status.DELIVERED
    print("e2e_state=delivered submissions=1 notification=1 autoreply=1")
elif phase == "maintenance":
    assert not ContactSubmission.objects.filter(
        form=contact_form, page_path="/e2e-expired/"
    ).exists()
    latest = ContactMaintenanceRun.objects.filter(
        kind=ContactMaintenanceRun.Kind.PURGE
    ).first()
    assert latest is not None
    assert latest.status == ContactMaintenanceRun.Status.SUCCEEDED
    assert latest.deleted_count == 1
    print("e2e_state=maintenance purged=1 status=succeeded")
else:
    raise SystemExit(f"unknown phase: {phase}")
