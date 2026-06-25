from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import License, LicenseSeat


def assign_license_seat(*, license: License, assigned_user=None, assigned_asset=None, note: str = "") -> LicenseSeat:
    seat = LicenseSeat(
        license=license,
        assigned_user=assigned_user,
        assigned_asset=assigned_asset,
        note=note,
    )
    seat.full_clean()
    seat.save()
    return seat


def release_license_seat(*, seat: LicenseSeat) -> LicenseSeat:
    if seat.released_at:
        raise ValidationError("License seat is already released.")
    seat.released_at = timezone.now()
    seat.save(update_fields=["released_at", "updated_at"])
    return seat
