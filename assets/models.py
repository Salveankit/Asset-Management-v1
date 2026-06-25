from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from core.models import TimestampedSoftDeleteModel
from locations.models import Location
from organisations.models import Company
from suppliers.models import Supplier


class DepreciationProfile(TimestampedSoftDeleteModel):
    class DepreciationType(models.TextChoices):
        STRAIGHT_LINE = "straight_line", "Straight Line"
        DECLINING_BALANCE = "declining_balance", "Declining Balance"
        AMOUNT = "amount", "Amount"

    name = models.CharField(max_length=255)
    months = models.PositiveIntegerField()
    depreciation_min = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    depreciation_type = models.CharField(
        max_length=32,
        choices=DepreciationType.choices,
        default=DepreciationType.AMOUNT,
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["name"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_depreciation_profile_name",
            )
        ]

    def __str__(self) -> str:
        return self.name


class AssetModel(TimestampedSoftDeleteModel):
    name = models.CharField(max_length=255)
    model_number = models.CharField(max_length=255, blank=True)
    manufacturer = models.ForeignKey(
        "catalogue.Manufacturer",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_models",
    )
    category = models.ForeignKey(
        "catalogue.Category",
        on_delete=models.PROTECT,
        related_name="asset_models",
        limit_choices_to={"category_type": "asset", "deleted_at__isnull": True},
    )
    depreciation = models.ForeignKey(
        DepreciationProfile,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_models",
    )
    eol_months = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["name", "model_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["name", "model_number"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_asset_model_name_number",
            )
        ]

    def __str__(self) -> str:
        return f"{self.name} {self.model_number}".strip()


class Asset(TimestampedSoftDeleteModel):
    asset_tag = models.CharField(max_length=100)
    name = models.CharField(max_length=255, blank=True)
    serial = models.CharField(max_length=255, blank=True)
    model = models.ForeignKey(
        AssetModel,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    status_label = models.ForeignKey(
        "catalogue.StatusLabel",
        on_delete=models.PROTECT,
        related_name="assets",
    )
    company = models.ForeignKey(
        Company,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets",
    )
    supplier = models.ForeignKey(
        Supplier,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets",
    )
    default_location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="default_assets",
    )
    assigned_user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_assets",
    )
    assigned_location = models.ForeignKey(
        Location,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checked_out_assets",
    )
    assigned_asset = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="child_assets",
    )
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    order_number = models.CharField(max_length=255, blank=True)
    warranty_months = models.PositiveIntegerField(default=0)
    requestable = models.BooleanField(default=False)
    checked_out_at = models.DateTimeField(null=True, blank=True)
    expected_checkin = models.DateField(null=True, blank=True)
    last_checkin = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["asset_tag"]
        constraints = [
            models.UniqueConstraint(
                fields=["asset_tag"],
                condition=Q(deleted_at__isnull=True),
                name="uniq_active_asset_tag",
            )
        ]

    def __str__(self) -> str:
        return self.display_name

    @property
    def display_name(self) -> str:
        return self.name or self.asset_tag

    @property
    def assignment_summary(self) -> str:
        if self.assigned_user:
            return f"User: {self.assigned_user}"
        if self.assigned_location:
            return f"Location: {self.assigned_location}"
        if self.assigned_asset:
            return f"Asset: {self.assigned_asset.display_name}"
        return "Available"

    @property
    def assignment_target_type(self) -> str:
        if self.assigned_user_id:
            return "user"
        if self.assigned_location_id:
            return "location"
        if self.assigned_asset_id:
            return "asset"
        return "none"

    @property
    def custody_state(self) -> str:
        return "checked_out" if self.checked_out_at else "available"

    def clear_assignment(self, *, mark_checkin: bool = False) -> None:
        self.assigned_user = None
        self.assigned_location = None
        self.assigned_asset = None
        self.expected_checkin = None
        self.checked_out_at = None
        if mark_checkin:
            self.last_checkin = timezone.now()

    def clean(self):
        super().clean()
        assignments = [
            bool(self.assigned_user_id),
            bool(self.assigned_location_id),
            bool(self.assigned_asset_id),
        ]
        if sum(assignments) > 1:
            raise ValidationError("An asset can only be assigned to one target at a time.")
        if self.assigned_asset_id and self.assigned_asset_id == self.id:
            raise ValidationError({"assigned_asset": "An asset cannot be assigned to itself."})
        if self.model_id and self.model.category.category_type != "asset":
            raise ValidationError({"model": "Asset records require an asset category model."})

        cleaned_serial = self.serial.strip()
        if cleaned_serial and Asset.objects.filter(deleted_at__isnull=True, serial__iexact=cleaned_serial).exclude(pk=self.pk).exists():
            raise ValidationError({"serial": "An active asset with this serial already exists."})

