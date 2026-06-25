from __future__ import annotations

from django.core.exceptions import ValidationError
from django.utils import timezone

from audits.models import AuditLog
from audits.services import log_asset_event

from .models import AssetCheckoutEvent


def _resolve_target(*, asset, target_type: str, user=None, location=None, related_asset=None):
    mapping = {
        "user": user,
        "location": location,
        "asset": related_asset,
    }
    target = mapping.get(target_type)
    if target_type not in mapping or target is None:
        raise ValidationError("Select a valid assignment target.")
    if target_type == "asset" and related_asset and related_asset.pk == asset.pk:
        raise ValidationError({"assigned_asset": "An asset cannot be assigned to itself."})
    if target_type == "user" and asset.company_id and getattr(user, "company_id", None) not in (None, asset.company_id):
        raise ValidationError("Assets can only be assigned to users in the same company.")
    if target_type == "asset" and asset.company_id and getattr(related_asset, "company_id", None) not in (None, asset.company_id):
        raise ValidationError("Assets can only be assigned to assets in the same company.")
    return target


def assign_asset(*, asset, actor, target_type: str, user=None, location=None, related_asset=None, note: str = ""):
    if asset.checked_out_at:
        raise ValidationError("Checked-out assets must be checked in before reassignment.")
    target = _resolve_target(
        asset=asset,
        target_type=target_type,
        user=user,
        location=location,
        related_asset=related_asset,
    )
    asset.clear_assignment(mark_checkin=False)
    if target_type == "user":
        asset.assigned_user = target
    elif target_type == "location":
        asset.assigned_location = target
    else:
        asset.assigned_asset = target
    asset.full_clean()
    asset.save()
    log_asset_event(
        asset=asset,
        actor=actor,
        action_type=AuditLog.ActionType.UPDATED,
        message=f"Asset assigned to {target_type}.",
        metadata={"target_type": target_type, "target_id": target.pk, "note": note},
    )
    return asset


def clear_asset_assignment(*, asset, actor, note: str = ""):
    if asset.assignment_target_type == "none":
        raise ValidationError("Asset is not currently assigned.")
    if asset.checked_out_at:
        raise ValidationError("Checked-out assets must be checked in instead of clearing assignment.")
    previous_target_type = asset.assignment_target_type
    asset.clear_assignment(mark_checkin=False)
    asset.save()
    log_asset_event(
        asset=asset,
        actor=actor,
        action_type=AuditLog.ActionType.UPDATED,
        message="Asset assignment cleared.",
        metadata={"previous_target_type": previous_target_type, "note": note},
    )
    return asset


def checkout_asset(
    *,
    asset,
    actor,
    target_type: str,
    user=None,
    location=None,
    related_asset=None,
    expected_checkin=None,
    note: str = "",
):
    if asset.checked_out_at:
        raise ValidationError("Asset is already checked out.")
    if not asset.status_label.deployable:
        raise ValidationError("Only deployable assets can be checked out.")
    target = _resolve_target(
        asset=asset,
        target_type=target_type,
        user=user,
        location=location,
        related_asset=related_asset,
    )
    asset.clear_assignment(mark_checkin=False)
    if target_type == "user":
        asset.assigned_user = target
    elif target_type == "location":
        asset.assigned_location = target
    else:
        asset.assigned_asset = target
    asset.checked_out_at = timezone.now()
    asset.expected_checkin = expected_checkin
    asset.full_clean()
    asset.save()
    AssetCheckoutEvent.objects.create(
        asset=asset,
        actor=actor,
        action_type=AssetCheckoutEvent.ActionType.CHECKOUT,
        target_type=target_type,
        target_user=user,
        target_location=location,
        target_asset=related_asset,
        expected_checkin=expected_checkin,
        note=note,
    )
    log_asset_event(
        asset=asset,
        actor=actor,
        action_type=AuditLog.ActionType.UPDATED,
        message=f"Asset checked out to {target_type}.",
        metadata={"target_type": target_type, "target_id": target.pk, "expected_checkin": str(expected_checkin or ""), "note": note},
    )
    return asset


def checkin_asset(*, asset, actor, note: str = ""):
    if not asset.checked_out_at:
        raise ValidationError("Asset is not currently checked out.")
    previous_target_type = asset.assignment_target_type
    previous_user = asset.assigned_user
    previous_location = asset.assigned_location
    previous_asset = asset.assigned_asset
    asset.clear_assignment(mark_checkin=True)
    asset.save()
    AssetCheckoutEvent.objects.create(
        asset=asset,
        actor=actor,
        action_type=AssetCheckoutEvent.ActionType.CHECKIN,
        target_type=previous_target_type,
        target_user=previous_user,
        target_location=previous_location,
        target_asset=previous_asset,
        note=note,
    )
    log_asset_event(
        asset=asset,
        actor=actor,
        action_type=AuditLog.ActionType.UPDATED,
        message="Asset checked in.",
        metadata={"previous_target_type": previous_target_type, "note": note},
    )
    return asset
