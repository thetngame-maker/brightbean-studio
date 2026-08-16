"""Lead/follow-up helpers for the Unified Social Inbox.

This layer intentionally stores CRM-style lead metadata in InboxMessage.extra so
it can ship without a schema migration or changes to provider ingestion.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount

from . import conversation_views, views
from .collaboration import record_activity
from .models import InboxMessage, InboxSLAConfig

INQUIRY_CHOICES = {
    "": "Not set",
    "trip_planning": "Planning a Trip",
    "trail_question": "Trail Question",
    "lodging": "Lodging",
    "event": "Event Question",
    "restaurant": "Restaurant",
    "attraction": "Attraction / Things to Do",
    "partnership": "Partnership",
    "business": "Business / Listing",
    "support": "General Support",
    "other": "Other",
}

STAGE_CHOICES = {
    "new": "New Lead",
    "qualified": "Qualified",
    "planning": "Planning",
    "booked": "Booked / Converted",
    "closed": "Closed",
    "lost": "Not Moving Forward",
}

PRIORITY_CHOICES = {
    "low": "Low",
    "normal": "Normal",
    "high": "High",
    "urgent": "Urgent",
}


def lead_profile_for_message(message):
    """Return normalized lead metadata for the selected conversation."""
    default = {
        "inquiry_type": "",
        "stage": "new",
        "priority": "normal",
        "follow_up_on": "",
    }

    conversation = conversation_views._conversation_queryset(message).order_by("received_at")
    rows = list(conversation)
    for row in reversed(rows):
        stored = dict((row.extra or {}).get("lead_profile") or {})
        if stored:
            result = default.copy()
            result.update({
                "inquiry_type": str(stored.get("inquiry_type") or ""),
                "stage": str(stored.get("stage") or "new"),
                "priority": str(stored.get("priority") or "normal"),
                "follow_up_on": str(stored.get("follow_up_on") or ""),
            })
            return _decorate(result)
    return _decorate(default)


def _decorate(profile):
    follow_up = parse_date(profile.get("follow_up_on") or "")
    today = timezone.localdate()
    return {
        **profile,
        "inquiry_label": INQUIRY_CHOICES.get(profile.get("inquiry_type"), "Other"),
        "stage_label": STAGE_CHOICES.get(profile.get("stage"), "New Lead"),
        "priority_label": PRIORITY_CHOICES.get(profile.get("priority"), "Normal"),
        "follow_up_date": follow_up,
        "follow_up_due": bool(follow_up and follow_up <= today and profile.get("stage") not in {"booked", "closed", "lost"}),
    }


def lead_context(message):
    return {
        "lead_profile": lead_profile_for_message(message),
        "inquiry_choices": INQUIRY_CHOICES.items(),
        "stage_choices": STAGE_CHOICES.items(),
        "priority_choices": PRIORITY_CHOICES.items(),
    }


@login_required
@require_permission("reply_from_inbox")
@require_POST
def save_lead_profile(request, workspace_id, message_id):
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(
        InboxMessage.objects.select_related("social_account", "assigned_to"),
        id=message_id,
        workspace=workspace,
    )
    if message.message_type != InboxMessage.MessageType.DM:
        return render(request, "inbox/partials/_customer_sidebar.html", {"workspace": workspace, "message": message})

    inquiry_type = request.POST.get("inquiry_type", "").strip()
    stage = request.POST.get("stage", "new").strip()
    priority = request.POST.get("priority", "normal").strip()
    follow_up_on = request.POST.get("follow_up_on", "").strip()

    if inquiry_type not in INQUIRY_CHOICES:
        inquiry_type = ""
    if stage not in STAGE_CHOICES:
        stage = "new"
    if priority not in PRIORITY_CHOICES:
        priority = "normal"
    parsed_follow_up = parse_date(follow_up_on) if follow_up_on else None
    if follow_up_on and not parsed_follow_up:
        follow_up_on = ""

    profile = {
        "inquiry_type": inquiry_type,
        "stage": stage,
        "priority": priority,
        "follow_up_on": follow_up_on,
    }

    conversation = conversation_views._conversation_queryset(message)
    changed = False
    previous = lead_profile_for_message(message)
    for row in conversation:
        extra = dict(row.extra or {})
        if dict(extra.get("lead_profile") or {}) != profile:
            changed = True
        extra["lead_profile"] = profile
        row.extra = extra
        row.save(update_fields=["extra"])

    if changed:
        summary = []
        if previous.get("inquiry_type") != inquiry_type and inquiry_type:
            summary.append(f"categorized this inquiry as {INQUIRY_CHOICES[inquiry_type]}")
        if previous.get("stage") != stage:
            summary.append(f"moved the lead to {STAGE_CHOICES[stage]}")
        if previous.get("priority") != priority:
            summary.append(f"set priority to {PRIORITY_CHOICES[priority]}")
        if previous.get("follow_up_on") != follow_up_on:
            summary.append(f"set follow-up for {follow_up_on}" if follow_up_on else "cleared the follow-up date")
        record_activity(message, request.user, ("; ".join(summary) or "updated lead details") + ".")

    message = conversation_views._refresh_message(message)
    context = {"workspace": workspace, "message": message}
    context.update(conversation_views._customer_profile_context(message))
    context.update(lead_context(message))
    return render(request, "inbox/partials/_customer_sidebar.html", context)


@login_required
@require_permission("use_inbox")
def followup_feed(request, workspace_id):
    """Inbox view containing conversations whose follow-up date is due today or earlier."""
    workspace = views._get_workspace(request, workspace_id)
    today = timezone.localdate().isoformat()

    qs = (
        InboxMessage.objects.for_workspace(workspace.id)
        .select_related("social_account", "assigned_to")
        .filter(message_type=InboxMessage.MessageType.DM)
        .exclude(extra__lead_profile__follow_up_on="")
        .filter(extra__lead_profile__follow_up_on__lte=today)
        .exclude(extra__lead_profile__stage__in=["booked", "closed", "lost"])
    )

    # Reuse the normal inbox filters where practical.
    platforms = request.GET.getlist("platform")
    if platforms:
        qs = qs.filter(social_account__platform__in=platforms)
    statuses = request.GET.getlist("status")
    if statuses:
        qs = qs.filter(status__in=statuses)
    q = request.GET.get("q", "").strip()
    if q:
        from django.db.models import Q
        qs = qs.filter(Q(body__icontains=q) | Q(sender_name__icontains=q) | Q(sender_handle__icontains=q))

    team_members = WorkspaceMembership.objects.filter(workspace=workspace).select_related("user")
    social_accounts = SocialAccount.objects.filter(
        workspace=workspace,
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    sla_config = InboxSLAConfig.objects.filter(workspace=workspace, is_active=True).first()

    context = {
        "workspace": workspace,
        "inbox_messages": qs[:views.MESSAGES_PER_PAGE],
        "sla_config": sla_config,
        "team_members": team_members,
        "social_accounts": social_accounts,
        "current_view": "followup",
        "active_filters": {
            "platform": platforms,
            "account": [],
            "type": ["dm"],
            "status": statuses,
            "assigned": "",
            "sentiment": [],
            "date_from": "",
            "date_to": "",
            "q": q,
        },
    }
    if request.htmx:
        return render(request, "inbox/partials/_message_list.html", context)
    return render(request, "inbox/feed.html", context)
