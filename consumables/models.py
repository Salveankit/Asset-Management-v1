from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class Consumable(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    category = models.ForeignKey(
        "catalogue.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumables",
        limit_choices_to={"category_type": "consumable", "deleted_at__isnull": True},
    )
    company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumables",
    )
    supplier = models.ForeignKey(
        "suppliers.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="consumables",
    )
    quantity = models.PositiveIntegerField(default=0)
    min_quantity = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "company"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_consumable_name_company",
            )
        ]

    @property
    def available_quantity(self) -> int:
        return self.quantity

    def __str__(self) -> str:
        return self.name

    def clean(self):
        super().clean()

        cleaned_name = self.name.strip()
        if not cleaned_name:
            return

        duplicates = Consumable.objects.filter(
            deleted_at__isnull=True,
            name__iexact=cleaned_name,
        ).exclude(pk=self.pk)

        duplicates = duplicates.filter(category_id=self.category_id) if self.category_id else duplicates.filter(category__isnull=True)
        duplicates = duplicates.filter(supplier_id=self.supplier_id) if self.supplier_id else duplicates.filter(supplier__isnull=True)

        if self.company_id:
            duplicates = duplicates.filter(Q(company_id=self.company_id) | Q(company__isnull=True))

        if duplicates.exists():
            raise ValidationError(
                {
                    "name": (
                        "An active consumable with the same name and sourcing metadata already exists. "
                        "Reuse the existing record instead of creating a duplicate."
                    )
                }
            )
