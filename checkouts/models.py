from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class AssetCheckoutEvent(models.Model):
    class ActionType(models.TextChoices):
        CHECKOUT = "checkout", "Check-out"
        CHECKIN = "checkin", "Check-in"

    TARGET_TYPE_CHOICES = (
        ("user", "User"),
        ("location", "Location"),
        ("asset", "Asset"),
        ("none", "None"),
    )

    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.CASCADE,
        related_name="checkout_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_checkout_events",
    )
    action_type = models.CharField(max_length=16, choices=ActionType.choices)
    target_type = models.CharField(max_length=16, choices=TARGET_TYPE_CHOICES, default="none")
    target_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkout_target_user_events",
    )
    target_location = models.ForeignKey(
        "locations.Location",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkout_target_location_events",
    )
    target_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="checkout_target_asset_events",
    )
    expected_checkin = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.get_action_type_display()} :: {self.asset}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Checkout events are immutable once written.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Checkout events cannot be deleted.")
