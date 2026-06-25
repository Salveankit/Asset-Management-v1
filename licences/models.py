from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import TimestampedSoftDeleteModel


class License(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    product_key = models.CharField(max_length=255, blank=True)
    reference_code = models.CharField(max_length=255, blank=True)
    seats = models.PositiveIntegerField(default=1)
    company = models.ForeignKey(
        "organisations.Company",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="licenses",
    )
    category = models.ForeignKey(
        "catalogue.Category",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="licenses",
        limit_choices_to={"category_type": "license", "deleted_at__isnull": True},
    )
    manufacturer = models.ForeignKey(
        "catalogue.Manufacturer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="licenses",
    )
    supplier = models.ForeignKey(
        "suppliers.Supplier",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="licenses",
    )
    depreciation = models.ForeignKey(
        "assets.DepreciationProfile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="licenses",
    )
    purchase_date = models.DateField(null=True, blank=True)
    expiration_date = models.DateField(null=True, blank=True)
    renewal_date = models.DateField(null=True, blank=True)
    billing_term = models.CharField(max_length=100, blank=True)
    order_number = models.CharField(max_length=255, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "company"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_license_name_company",
            )
        ]

    def __str__(self) -> str:
        return self.name

    @property
    def assigned_seat_count(self) -> int:
        return self.seats_assigned.filter(released_at__isnull=True).count()

    @property
    def available_seat_count(self) -> int:
        return max(self.seats - self.assigned_seat_count, 0)

    def clean(self):
        super().clean()
        if self.pk and self.assigned_seat_count > self.seats:
            raise ValidationError({"seats": "Seat count cannot be lower than active assignments."})

        cleaned_name = self.name.strip()
        if not cleaned_name:
            return

        duplicates = License.objects.filter(
            deleted_at__isnull=True,
            name__iexact=cleaned_name,
        ).exclude(pk=self.pk)

        duplicates = duplicates.filter(category_id=self.category_id) if self.category_id else duplicates.filter(category__isnull=True)
        duplicates = duplicates.filter(manufacturer_id=self.manufacturer_id) if self.manufacturer_id else duplicates.filter(manufacturer__isnull=True)
        duplicates = duplicates.filter(supplier_id=self.supplier_id) if self.supplier_id else duplicates.filter(supplier__isnull=True)
        duplicates = duplicates.filter(purchase_date=self.purchase_date) if self.purchase_date else duplicates.filter(purchase_date__isnull=True)
        duplicates = duplicates.filter(expiration_date=self.expiration_date) if self.expiration_date else duplicates.filter(expiration_date__isnull=True)
        duplicates = duplicates.filter(renewal_date=self.renewal_date) if self.renewal_date else duplicates.filter(renewal_date__isnull=True)
        duplicates = duplicates.filter(billing_term__iexact=self.billing_term.strip()) if self.billing_term.strip() else duplicates.filter(billing_term="")
        duplicates = duplicates.filter(order_number__iexact=self.order_number.strip()) if self.order_number.strip() else duplicates.filter(order_number="")
        duplicates = duplicates.filter(product_key__iexact=self.product_key.strip()) if self.product_key.strip() else duplicates.filter(product_key="")
        duplicates = duplicates.filter(reference_code__iexact=self.reference_code.strip()) if self.reference_code.strip() else duplicates.filter(reference_code="")

        if self.company_id:
            duplicates = duplicates.filter(Q(company_id=self.company_id) | Q(company__isnull=True))

        if duplicates.exists():
            raise ValidationError(
                {
                    "name": (
                        "An active license with the same entitlement and sourcing metadata already exists. "
                        "Reuse the existing record instead of creating a duplicate."
                    )
                }
            )


class LicenseSeat(TimestampedSoftDeleteModel):
    license = models.ForeignKey(
        License,
        on_delete=models.CASCADE,
        related_name="seats_assigned",
    )
    assigned_user = models.ForeignKey(
        "accounts.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="license_seats",
    )
    assigned_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="license_seats",
    )
    note = models.TextField(blank=True)
    assigned_at = models.DateTimeField(auto_now_add=True)
    released_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-assigned_at"]

    def __str__(self) -> str:
        return f"{self.license} seat"

    def clean(self):
        super().clean()
        if bool(self.assigned_user_id) == bool(self.assigned_asset_id):
            raise ValidationError("A license seat must be assigned to exactly one target.")
        if not self.pk and self.license.available_seat_count <= 0:
            raise ValidationError({"license": "No seats are available for this license."})
