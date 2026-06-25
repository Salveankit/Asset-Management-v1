from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class Supplier(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=250, blank=True)
    address2 = models.CharField(max_length=250, blank=True)
    city = models.CharField(max_length=191, blank=True)
    state = models.CharField(max_length=191, blank=True)
    country = models.CharField(max_length=191, blank=True)
    zip_code = models.CharField(max_length=20, blank=True)
    phone = models.CharField(max_length=35, blank=True)
    fax = models.CharField(max_length=35, blank=True)
    email = models.EmailField(blank=True)
    contact = models.CharField(max_length=100, blank=True)
    url = models.URLField(blank=True)
    tag_color = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_supplier_name",
            )
        ]

    def __str__(self) -> str:
        return self.name
