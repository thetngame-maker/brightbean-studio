from __future__ import annotations

from typing import Any

from .models import AuditEvent


def record_audit_event(
    *,
    workspace,
    action: str,
    actor=None,
    target: Any | None = None,
    target_type: str = "",
    target_id: str = "",
    target_label: str = "",
    metadata: dict | None = None,
    request=None,
    source: str = AuditEvent.Source.UI,
) -> AuditEvent:
    """Write one sanitized audit event.

    This helper intentionally accepts primitive target fields as well as a
    Django model instance so callers in moderation/API/system code can use the
    same trail without coupling apps together.
    """

    if target is not None:
        target_type = target_type or getattr(getattr(target, "_meta", None), "label_lower", "")
        target_id = target_id or str(getattr(target, "pk", "") or "")
        target_label = target_label or str(target)[:255]

    ip_address = None
    user_agent = ""
    if request is not None:
        # Use REMOTE_ADDR only; forwarded headers are untrusted unless a
        # deployment has explicitly configured trusted proxies elsewhere.
        ip_address = request.META.get("REMOTE_ADDR") or None
        user_agent = (request.META.get("HTTP_USER_AGENT") or "")[:500]

    return AuditEvent.objects.create(
        workspace=workspace,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action[:100],
        target_type=(target_type or "")[:100],
        target_id=(target_id or "")[:255],
        target_label=(target_label or "")[:255],
        source=source,
        metadata=metadata or {},
        ip_address=ip_address,
        user_agent=user_agent,
    )
