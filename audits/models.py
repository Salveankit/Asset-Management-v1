from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import models


class AuditLog(models.Model):
    class ActionType(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        ARCHIVED = "archived", "Archived"
        ATTACHMENT_ADDED = "attachment_added", "Attachment Added"

    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="audit_logs",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_audit_logs",
    )
    action_type = models.CharField(max_length=32, choices=ActionType.choices)
    message = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_action_type_display()} :: {self.asset}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Audit logs are immutable once written.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Audit logs cannot be deleted.")
