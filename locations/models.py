from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from accounts.models import User
from core.models import TimestampedSoftDeleteModel


class Location(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children",
    )
    manager = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_locations",
    )
    currency = models.CharField(max_length=8, default="$")
    address = models.CharField(max_length=191, blank=True)
    address2 = models.CharField(max_length=191, blank=True)
    city = models.CharField(max_length=191, blank=True)
    state = models.CharField(max_length=191, blank=True)
    country = models.CharField(max_length=191, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=35, blank=True)
    fax = models.CharField(max_length=35, blank=True)
    ldap_ou = models.CharField(max_length=191, blank=True)
    tag_color = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_location_name",
            )
        ]

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.pk and self.parent_id == self.id:
            raise ValidationError({"parent": "A location cannot be its own parent."})
        ancestor = self.parent
        while ancestor:
            if ancestor.pk == self.pk:
                raise ValidationError({"parent": "Parent relationship cannot be circular."})
            ancestor = ancestor.parent
