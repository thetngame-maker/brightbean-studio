"""Derived collaboration milestones backed by canonical workflow state."""

from __future__ import annotations

from django.utils import timezone

from .models import UGCCreatorCollaboration
from .ugc_creator_services import rights_can_use

MILESTONE_COUNT = 6
CLOSED_STATUSES = {
    UGCCreatorCollaboration.Status.DECLINED,
    UGCCreatorCollaboration.Status.CANCELLED,
}
CONFIRMED_STATUSES = {
    UGCCreatorCollaboration.Status.CONFIRMED,
    UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
    UGCCreatorCollaboration.Status.COMPLETED,
}
DELIVERED_STATUSES = {
    UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
    UGCCreatorCollaboration.Status.COMPLETED,
}


def _rights_state(collaboration):
    if not collaboration.submission_id:
        return False, "Link the delivered content before checking its Rights Passport."
    for scope in collaboration.requested_rights or ["organic_social"]:
        allowed, reason = rights_can_use(collaboration.submission, scope=scope)
        if not allowed:
            return False, reason
    return True, "Every requested usage is active on the linked Rights Passport."


def _milestone(key, label, description, *, state="upcoming", due_at=None):
    return {
        "key": key,
        "label": label,
        "description": description,
        "state": state,
        "complete": state == "complete",
        "due_at": due_at,
        "overdue": False,
    }


def collaboration_milestone_summary(collaboration, *, now=None):
    """Return a presentation-ready milestone timeline without separate state."""
    now = now or timezone.now()
    closed = collaboration.status in CLOSED_STATUSES
    invitation_complete = bool(
        collaboration.invited_at
        or collaboration.status
        not in {
            UGCCreatorCollaboration.Status.DRAFT,
            UGCCreatorCollaboration.Status.CANCELLED,
        }
    )
    confirmed_complete = collaboration.status in CONFIRMED_STATUSES
    delivered_complete = collaboration.status in DELIVERED_STATUSES
    if delivered_complete:
        rights_ready, rights_reason = _rights_state(collaboration)
    else:
        rights_ready = False
        rights_reason = "Rights will be checked on the delivered content before completion."
    rights_complete = delivered_complete and rights_ready
    workflow_complete = collaboration.status == UGCCreatorCollaboration.Status.COMPLETED

    milestones = [
        _milestone(
            "brief",
            "Brief prepared",
            "Deliverables, target, timing, and offer are recorded.",
            state="complete",
        ),
        _milestone(
            "invited",
            "Invitation sent",
            "Secure creator invitation has been sent."
            if invitation_complete
            else "Create the secure creator link, send it, and mark the invitation sent.",
            state="complete" if invitation_complete else "current",
        ),
        _milestone(
            "confirmed",
            "Creator confirmed",
            "The creator accepted the collaboration."
            if confirmed_complete
            else (
                "Confirm final details with the interested creator."
                if collaboration.status == UGCCreatorCollaboration.Status.INTERESTED
                else "Waiting for the creator to accept or decline."
            ),
            state="complete" if confirmed_complete else ("current" if invitation_complete else "upcoming"),
        ),
        _milestone(
            "delivered",
            "Content delivered",
            "Delivered content is recorded."
            if delivered_complete
            else (
                "Content is linked; mark it received when the delivery is complete."
                if collaboration.submission_id
                else "Waiting for the agreed creator deliverables."
            ),
            state="complete" if delivered_complete else ("current" if confirmed_complete else "upcoming"),
            due_at=collaboration.content_due_at,
        ),
        _milestone(
            "rights",
            "Rights cleared",
            rights_reason,
            state="complete" if rights_complete else ("blocked" if delivered_complete else "upcoming"),
        ),
        _milestone(
            "completed",
            "Collaboration complete",
            "The brief, delivery, and usage rights are complete."
            if workflow_complete
            else "Complete the collaboration after every requested usage right is active.",
            state="complete" if workflow_complete else ("current" if rights_complete else "upcoming"),
        ),
    ]

    if closed:
        for milestone in milestones:
            if not milestone["complete"]:
                milestone["state"] = "closed"
    delivery = next(item for item in milestones if item["key"] == "delivered")
    if (
        not closed
        and not delivery["complete"]
        and delivery["state"] == "current"
        and delivery["due_at"]
        and delivery["due_at"] < now
    ):
        delivery["overdue"] = True
        delivery["state"] = "blocked"
        elapsed = now - delivery["due_at"]
        days = max(1, elapsed.days)
        delivery["description"] = f"Delivery is overdue by {days} day{'s' if days != 1 else ''}."

    completed_count = sum(item["complete"] for item in milestones)
    next_milestone = next((item for item in milestones if item["state"] in {"current", "blocked"}), None)
    blocked = next((item for item in milestones if item["state"] == "blocked"), None)
    return {
        "items": milestones,
        "completed_count": completed_count,
        "total_count": MILESTONE_COUNT,
        "progress_percent": round((completed_count / MILESTONE_COUNT) * 100),
        "next": next_milestone,
        "blocked": blocked,
        "at_risk": blocked is not None,
        "closed": closed,
    }
