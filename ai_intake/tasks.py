from __future__ import annotations

from celery import shared_task
from django.contrib.auth import get_user_model

from .models import AIIntakeDocument
from .services import process_document_line_items


@shared_task(name="ai_intake.process_document_line_items")
def process_document_line_items_task(*, document_id: int, actor_id: int | None = None) -> None:
    try:
        document = AIIntakeDocument.objects.get(pk=document_id)
    except AIIntakeDocument.DoesNotExist:
        return

    actor = None
    if actor_id is not None:
        actor = get_user_model().objects.filter(pk=actor_id).first()

    process_document_line_items(document=document, actor=actor)
