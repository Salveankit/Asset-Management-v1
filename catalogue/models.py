from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class Category(TimestampedSoftDeleteModel):
    class CategoryType(models.TextChoices):
        ASSET = "asset", "Asset"
        ACCESSORY = "accessory", "Accessory"
        CONSUMABLE = "consumable", "Consumable"
        COMPONENT = "component", "Component"
        LICENSE = "license", "License"

    name = models.CharField(max_length=255)
    category_type = models.CharField(max_length=32, choices=CategoryType.choices)
    require_acceptance = models.BooleanField(default=False)
    use_default_eula = models.BooleanField(default=False)
    checkin_email = models.BooleanField(default=False)
    alert_on_response = models.BooleanField(default=False)
    tag_color = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "category_type"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_category_name_type",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.category_type})"


class Manufacturer(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    url = models.URLField(blank=True)
    support_email = models.EmailField(blank=True)
    support_phone = models.CharField(max_length=50, blank=True)
    support_url = models.URLField(blank=True)
    warranty_lookup_url = models.URLField(blank=True)
    tag_color = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_manufacturer_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class StatusLabel(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    deployable = models.BooleanField(default=True)
    pending = models.BooleanField(default=False)
    archived = models.BooleanField(default=False)
    color = models.CharField(max_length=20, blank=True)
    show_in_nav = models.BooleanField(default=False)
    default_label = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_status_label_name",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def status_type(self) -> str:
        if self.pending and not self.archived and not self.deployable:
            return "pending"
        if self.archived and not self.pending and not self.deployable:
            return "archived"
        if not self.pending and not self.archived and not self.deployable:
            return "undeployable"
        return "deployable"

    @staticmethod
    def flags_from_type(status_type: str | None) -> dict[str, bool]:
        mapping = {
            "deployable": {"deployable": True, "pending": False, "archived": False},
            "pending": {"deployable": False, "pending": True, "archived": False},
            "undeployable": {"deployable": False, "pending": False, "archived": False},
            "archived": {"deployable": False, "pending": False, "archived": True},
        }
        if status_type not in mapping:
            raise ValidationError({"status_type": "Select a valid status type."})
        return mapping[status_type]
