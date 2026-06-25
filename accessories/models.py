from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class Accessory(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        "catalogue.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accessories",
        limit_choices_to={"category_type": "accessory", "deleted_at__isnull": True},
    )
    company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accessories",
    )
    supplier = models.ForeignKey(
        "suppliers.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accessories",
    )
    location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="accessories",
    )
    quantity = models.PositiveIntegerField(default=0)
    assigned_quantity = models.PositiveIntegerField(default=0)
    min_quantity = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "company"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_accessory_name_company",
            )
        ]

    @property
    def available_quantity(self) -> int:
        return max(self.quantity - self.assigned_quantity, 0)

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()
        if self.assigned_quantity > self.quantity:
            raise ValidationError({"assigned_quantity": "Assigned quantity cannot exceed total quantity."})

        cleaned_name = self.name.strip()
        if not cleaned_name:
            return

        duplicates = Accessory.objects.filter(
            deleted_at__isnull=True,
            name__iexact=cleaned_name,
        ).exclude(pk=self.pk)

        duplicates = duplicates.filter(category_id=self.category_id) if self.category_id else duplicates.filter(category__isnull=True)
        duplicates = duplicates.filter(supplier_id=self.supplier_id) if self.supplier_id else duplicates.filter(supplier__isnull=True)
        duplicates = duplicates.filter(location_id=self.location_id) if self.location_id else duplicates.filter(location__isnull=True)

        if self.company_id:
            duplicates = duplicates.filter(Q(company_id=self.company_id) | Q(company__isnull=True))

        if duplicates.exists():
            raise ValidationError(
                {
                    "name": (
                        "An active accessory with the same name and sourcing metadata already exists. "
                        "Reuse the existing record instead of creating a duplicate."
                    )
                }
            )
