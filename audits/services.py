from __future__ import annotations

from collections.abc import Mapping

from .models import AuditLog


def log_asset_event(*, asset, action_type: str, message: str, actor=None, metadata: Mapping | None = None) -> AuditLog:
    return AuditLog.objects.create(
        asset=asset,
        actor=actor,
        action_type=action_type,
        message=message,
        metadata=dict(metadata or {}),
    )
