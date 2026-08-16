"""Lead dashboard for the Unified Social Inbox.

Uses the migration-free lead metadata already stored in InboxMessage.extra.
"""

from collections import Counter
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.members.decorators import require_permission

from . import views
from .lead_views import INQUIRY_CHOICES, PRIORITY_CHOICES, STAGE_CHOICES
from .models import InboxMessage

CLOSED_STAGES = {"booked", "closed", "lost"}


def _conversation_key(message):
    return (message.social_account_id, message.sender_handle or str(message.id))


def _profile(message):
    raw = dict((message.extra or {}).get("lead_profile") or {})
    return {
        "inquiry_type": str(raw.get("inquiry_type") or ""),
        "stage": str(raw.get("stage") or "new"),
        "priority": str(raw.get("priority") or "normal"),
        "follow_up_on": str(raw.get("follow_up_on") or ""),
    }


def _customer_profile(message):
    extra = dict(message.extra or {})
    customer = dict(extra.get("customer_profile") or {})
    return {
        "email": str(customer.get("email") or ""),
        "phone": str(customer.get("phone") or ""),
        "location": str(customer.get("location") or ""),
        "tags": [str(tag) for tag in (extra.get("customer_tags") or []) if str(tag).strip()],
    }


@login_required
@require_permission("use_inbox")
def lead_dashboard(request, workspace_id):
    workspace = views._get_workspace(request, workspace_id)
    today = timezone.localdate()
    upcoming_cutoff = today + timedelta(days=7)

    # Newest row wins for each person/account conversation. Lead/customer metadata
    # is mirrored across the whole conversation, so this yields one CRM record.
    rows = (
        InboxMessage.objects.for_workspace(workspace.id)
        .select_related("social_account", "assigned_to")
        .filter(message_type=InboxMessage.MessageType.DM)
        .order_by("-received_at")
    )
    latest = {}
    for message in rows:
        key = _conversation_key(message)
        if key not in latest:
            latest[key] = message

    leads = []
    stage_counts = Counter()
    inquiry_counts = Counter()
    priority_counts = Counter()
    overdue = []
    due_today = []
    upcoming = []

    for message in latest.values():
        profile = _profile(message)
        customer = _customer_profile(message)
        follow_up_date = parse_date(profile["follow_up_on"]) if profile["follow_up_on"] else None
        stage = profile["stage"] if profile["stage"] in STAGE_CHOICES else "new"
        inquiry = profile["inquiry_type"] if profile["inquiry_type"] in INQUIRY_CHOICES else ""
        priority = profile["priority"] if profile["priority"] in PRIORITY_CHOICES else "normal"

        # Only conversations that have actually been touched as leads contribute
        # to the CRM funnel. A follow-up, category, non-default stage, or priority
        # is enough to make it a tracked lead.
        tracked = bool(inquiry or follow_up_date or stage != "new" or priority != "normal")
        if not tracked:
            continue

        stage_counts[stage] += 1
        inquiry_counts[inquiry or "other"] += 1
        priority_counts[priority] += 1

        item = {
            "message": message,
            "name": message.sender_name or message.sender_handle or "Customer",
            "handle": message.sender_handle or "",
            "platform": message.social_account.platform,
            "account_name": getattr(message.social_account, "display_name", "") or getattr(message.social_account, "account_name", "") or "",
            "stage": stage,
            "stage_label": STAGE_CHOICES.get(stage, "New Lead"),
            "inquiry_type": inquiry,
            "inquiry_label": INQUIRY_CHOICES.get(inquiry, "Other"),
            "priority": priority,
            "priority_label": PRIORITY_CHOICES.get(priority, "Normal"),
            "follow_up_date": follow_up_date,
            "customer": customer,
            "owner": message.assigned_to,
            "last_contact": message.received_at,
            "url": reverse("inbox:feed", kwargs={"workspace_id": workspace.id}) + f"?message={message.id}",
        }
        leads.append(item)

        if follow_up_date and stage not in CLOSED_STAGES:
            if follow_up_date < today:
                overdue.append(item)
            elif follow_up_date == today:
                due_today.append(item)
            elif follow_up_date <= upcoming_cutoff:
                upcoming.append(item)

    priority_rank = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
    for bucket in (overdue, due_today, upcoming):
        bucket.sort(key=lambda item: (item["follow_up_date"] or today, priority_rank.get(item["priority"], 2), item["name"].lower()))

    total = len(leads)
    converted = stage_counts.get("booked", 0)
    active = sum(stage_counts.get(stage, 0) for stage in ("new", "qualified", "planning"))
    conversion_rate = round((converted / total) * 100) if total else 0

    stage_cards = [
        {"key": key, "label": label, "count": stage_counts.get(key, 0)}
        for key, label in STAGE_CHOICES.items()
    ]
    inquiry_cards = sorted(
        [
            {"key": key, "label": INQUIRY_CHOICES.get(key, "Other"), "count": count}
            for key, count in inquiry_counts.items()
        ],
        key=lambda item: (-item["count"], item["label"]),
    )

    context = {
        "workspace": workspace,
        "today": today,
        "total_leads": total,
        "active_leads": active,
        "converted_leads": converted,
        "conversion_rate": conversion_rate,
        "overdue": overdue,
        "due_today": due_today,
        "upcoming": upcoming,
        "overdue_count": len(overdue),
        "today_count": len(due_today),
        "upcoming_count": len(upcoming),
        "stage_cards": stage_cards,
        "inquiry_cards": inquiry_cards,
        "recent_leads": sorted(leads, key=lambda item: item["last_contact"], reverse=True)[:12],
    }
    return render(request, "inbox/lead_dashboard.html", context)
