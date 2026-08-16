"""Permission-state helpers for externally discovered community content.

Discovered content is not equivalent to a contributor submission. These
helpers keep outreach/permission state in UGCSubmission.metadata so external
discovery providers can share one workflow without provider-specific schema.
"""

from __future__ import annotations

from typing import Any

PERMISSION_KEY = "permission"

NOT_CONTACTED = "not_contacted"
REQUESTED = "requested"
GRANTED = "granted"
DECLINED = "declined"

VALID_PERMISSION_STATUSES = {NOT_CONTACTED, REQUESTED, GRANTED, DECLINED}

PERMISSION_LABELS = {
    NOT_CONTACTED: "Not contacted",
    REQUESTED: "Permission requested",
    GRANTED: "Permission granted",
    DECLINED: "Permission declined",
}


def normalize_permission_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    return status if status in VALID_PERMISSION_STATUSES else NOT_CONTACTED


def get_permission(metadata: Any) -> dict[str, str]:
    if not isinstance(metadata, dict):
        return {"status": NOT_CONTACTED, "channel": "", "note": "", "updated_at": ""}
    raw = metadata.get(PERMISSION_KEY)
    if not isinstance(raw, dict):
        return {"status": NOT_CONTACTED, "channel": "", "note": "", "updated_at": ""}
    return {
        "status": normalize_permission_status(raw.get("status")),
        "channel": str(raw.get("channel") or "").strip()[:50],
        "note": str(raw.get("note") or "").strip()[:1000],
        "updated_at": str(raw.get("updated_at") or "").strip()[:100],
    }


def set_permission(
    metadata: Any,
    *,
    status: Any,
    channel: Any = "",
    note: Any = "",
    updated_at: Any = "",
) -> dict[str, Any]:
    result = dict(metadata) if isinstance(metadata, dict) else {}
    result[PERMISSION_KEY] = {
        "status": normalize_permission_status(status),
        "channel": str(channel or "").strip()[:50],
        "note": str(note or "").strip()[:1000],
        "updated_at": str(updated_at or "").strip()[:100],
    }
    return result


def permission_label(metadata: Any) -> str:
    return PERMISSION_LABELS[get_permission(metadata)["status"]]
