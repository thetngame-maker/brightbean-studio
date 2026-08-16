"""Conversation-level actions for the Unified Social Inbox.

Direct messages are stored as individual inbound rows because each platform
message has its own immutable platform ID. The UI, however, should treat a DM
thread with one person as a single work item. These views provide that layer
without changing the storage model or the working provider send path.
"""

from django.contrib.auth.decorators import login_required
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership
from apps.notifications.engine import notify
from apps.notifications.models import EventType

from . import views
from .collaboration import inbox_action_url, record_activity, user_display_name
from .forms import AssignForm, StatusForm
from .models import InboxMessage


def _conversation_queryset(message):
    """Return all inbound rows belonging to the selected DM conversation."""
    if message.message_type != InboxMessage.MessageType.DM or not message.sender_handle:
        return InboxMessage.objects.filter(pk=message.pk)

    return InboxMessage.objects.filter(
        workspace=message.workspace,
        social_account=message.social_account,
        message_type=InboxMessage.MessageType.DM,
        sender_handle=message.sender_handle,
    )


def _refresh_message(message):
    return InboxMessage.objects.select_related("social_account", "assigned_to").get(pk=message.pk)


def _customer_identity(message):
    return {
        "name": message.sender_name or message.sender_handle or "Customer",
        "handle": message.sender_handle or "",
        "avatar_url": message.sender_avatar_url or "",
    }


def _customer_profile_context(message):
    """Build a lightweight CRM profile from conversation metadata.

    This deliberately lives in InboxMessage.extra so it can ship without a
    migration and without changing the platform ingestion/reply path.
    """
    conversation = _conversation_queryset(message).order_by("received_at")
    rows = list(conversation)
    profile = {"email": "", "phone": "", "location": ""}
    tags = []

    # Prefer the newest stored profile so older rows can safely remain as a
    # historical copy if a future integration changes how metadata is written.
    for row in reversed(rows):
        extra = dict(row.extra or {})
        stored = extra.get("customer_profile") or {}
        if stored:
            profile.update(
                {
                    "email": str(stored.get("email") or ""),
                    "phone": str(stored.get("phone") or ""),
                    "location": str(stored.get("location") or ""),
                }
            )
            tags = [str(tag) for tag in (extra.get("customer_tags") or []) if str(tag).strip()]
            break

    reply_count = conversation.aggregate(total=Count("replies"))["total"] or 0
    first_seen = rows[0].received_at if rows else message.received_at
    last_seen = rows[-1].received_at if rows else message.received_at

    return {
        "customer_identity": _customer_identity(message),
        "customer_profile": profile,
        "customer_tags": tags,
        "conversation_stats": {
            "inbound_count": len(rows) or 1,
            "reply_count": reply_count,
            "first_seen": first_seen,
            "last_seen": last_seen,
        },
    }


def _detail_context(workspace, message):
    context = views._detail_context(workspace, message)
    if message.message_type == InboxMessage.MessageType.DM:
        context.update(_customer_profile_context(message))
    return context


@login_required
@require_permission("use_inbox")
def conversation_detail(request, workspace_id, message_id):
    """Open a message panel, treating a DM sender thread as one work item.

    A new inbound DM re-opens a previously resolved conversation. Therefore if
    any row in the conversation is unread, opening the thread moves every row to
    OPEN. That gives the header, list row, filters and assignment state one
    consistent conversation status.
    """
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(
        InboxMessage.objects.select_related("social_account", "assigned_to"),
        id=message_id,
        workspace=workspace,
    )

    conversation = _conversation_queryset(message)
    if conversation.filter(status=InboxMessage.Status.UNREAD).exists():
        conversation.update(status=InboxMessage.Status.OPEN)
        message = _refresh_message(message)

    context = _detail_context(workspace, message)
    if request.htmx:
        return render(request, "inbox/partials/_message_panel.html", context)
    return render(request, "inbox/message_detail.html", context)


@login_required
@require_permission("reply_from_inbox")
@require_POST
def conversation_customer_profile(request, workspace_id, message_id):
    """Save lightweight customer details and tags for a DM conversation."""
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(
        InboxMessage.objects.select_related("social_account", "assigned_to"),
        id=message_id,
        workspace=workspace,
    )
    if message.message_type != InboxMessage.MessageType.DM:
        return HttpResponse("Customer profiles are available for conversations.", status=400)

    profile = {
        "email": request.POST.get("email", "").strip()[:254],
        "phone": request.POST.get("phone", "").strip()[:80],
        "location": request.POST.get("location", "").strip()[:160],
    }
    raw_tags = request.POST.get("tags", "")
    tags = []
    seen = set()
    for raw_tag in raw_tags.split(","):
        tag = raw_tag.strip()[:40]
        key = tag.lower()
        if tag and key not in seen:
            tags.append(tag)
            seen.add(key)
        if len(tags) >= 10:
            break

    conversation = _conversation_queryset(message)
    changed = False
    for row in conversation:
        extra = dict(row.extra or {})
        if extra.get("customer_profile") != profile or extra.get("customer_tags") != tags:
            changed = True
        extra["customer_profile"] = profile
        extra["customer_tags"] = tags
        row.extra = extra
        row.save(update_fields=["extra"])

    if changed:
        record_activity(message, request.user, "updated customer details.")

    message = _refresh_message(message)
    context = {"workspace": workspace, "message": message}
    context.update(_customer_profile_context(message))
    return render(request, "inbox/partials/_customer_sidebar.html", context)


@login_required
@require_permission("reply_from_inbox")
@require_POST
def conversation_change_status(request, workspace_id, message_id):
    """Apply a status change to the entire DM conversation."""
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(InboxMessage, id=message_id, workspace=workspace)

    form = StatusForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Invalid status.", status=400)

    new_status = form.cleaned_data["status"]
    old_status = message.status
    _conversation_queryset(message).update(status=new_status)
    message = _refresh_message(message)

    if old_status != new_status:
        label = dict(InboxMessage.Status.choices).get(new_status, new_status)
        record_activity(message, request.user, f"changed the conversation status to {label}.")

    context = _detail_context(workspace, message)
    return render(request, "inbox/partials/_message_panel.html", context)


@login_required
@require_permission("reply_from_inbox")
@require_POST
def conversation_assign(request, workspace_id, message_id):
    """Assign or unassign the entire DM conversation as one work item."""
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(
        InboxMessage.objects.select_related("assigned_to"),
        id=message_id,
        workspace=workspace,
    )

    form = AssignForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Invalid assignment.", status=400)

    previous_assignee_id = message.assigned_to_id
    assigned_to = None
    assigned_to_id = form.cleaned_data.get("assigned_to")
    if assigned_to_id:
        membership = (
            WorkspaceMembership.objects.filter(workspace=workspace, user_id=assigned_to_id)
            .select_related("user")
            .first()
        )
        if not membership:
            return HttpResponse("User is not a workspace member.", status=400)
        assigned_to = membership.user

    _conversation_queryset(message).update(assigned_to=assigned_to)
    message = _refresh_message(message)

    if previous_assignee_id != (assigned_to.id if assigned_to else None):
        if assigned_to:
            target = "themselves" if assigned_to == request.user else user_display_name(assigned_to)
            record_activity(message, request.user, f"assigned this conversation to {target}.")
        else:
            record_activity(message, request.user, "moved this conversation to Unassigned.")

    if assigned_to and assigned_to != request.user:
        notify(
            user=assigned_to,
            event_type=EventType.NEW_INBOX_MESSAGE,
            title=f"You were assigned a conversation from {message.sender_name}",
            body=message.body[:100],
            data={
                "message_id": str(message.id),
                "workspace_id": str(workspace.id),
                "action_url": inbox_action_url(message),
            },
        )

    context = _detail_context(workspace, message)
    return render(request, "inbox/partials/_message_panel.html", context)


@login_required
@require_permission("reply_from_inbox")
@require_POST
def conversation_send_reply(request, workspace_id, message_id):
    """Use the proven send path, then synchronize conversation status and ownership."""
    workspace = views._get_workspace(request, workspace_id)
    message = get_object_or_404(InboxMessage, id=message_id, workspace=workspace)

    response = views.send_reply(request, workspace_id, message_id)
    if response.status_code >= 400 or response.get("HX-Reply-Failed"):
        return response

    message = InboxMessage.objects.get(pk=message.pk)
    conversation = _conversation_queryset(message)

    # Successfully replying to an unassigned conversation claims it for the
    # teammate who answered. Never overwrite another teammate's ownership.
    claimed = False
    if not conversation.filter(assigned_to__isnull=False).exists():
        conversation.update(assigned_to=request.user)
        claimed = True

    if message.status == InboxMessage.Status.RESOLVED:
        conversation.update(status=InboxMessage.Status.RESOLVED)
    elif conversation.filter(status=InboxMessage.Status.UNREAD).exists():
        conversation.update(status=InboxMessage.Status.OPEN)

    if claimed:
        message = _refresh_message(message)
        record_activity(message, request.user, "took ownership after replying.")

    return response
