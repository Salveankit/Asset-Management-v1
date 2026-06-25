import os
import uuid

from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from core.models import TimestampedSoftDeleteModel


def asset_attachment_upload_to(instance, filename: str) -> str:
    extension = os.path.splitext(filename)[1].lower()
    return f"asset-attachments/{instance.asset_id}/{uuid.uuid4().hex}{extension}"


class AssetAttachment(TimestampedSoftDeleteModel):
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_asset_attachments",
    )
    file = models.FileField(
        upload_to=asset_attachment_upload_to,
        validators=[
            FileExtensionValidator(
                allowed_extensions=[
                    "pdf",
                    "png",
                    "jpg",
                    "jpeg",
                    "gif",
                    "webp",
                    "txt",
                    "csv",
                    "doc",
                    "docx",
                    "xls",
                    "xlsx",
                ]
            )
        ],
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return self.original_filename
