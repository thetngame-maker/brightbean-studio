"""Lightweight server-rendered Community Content Missions."""

from datetime import datetime, time
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCContentMission
from .ugc_content_missions import decorate_content_mission
from .ugc_creator_views import _safe_local_path
from .ugc_target_catalog import find_catalog_target, target_choices
from .ugc_views import _get_workspace

MISSION_PAGE_SIZE = 12
MISSION_FILTERS = {
    "active": {UGCContentMission.Status.ACTIVE},
    "planning": {UGCContentMission.Status.DRAFT, UGCContentMission.Status.PAUSED},
    "done": {UGCContentMission.Status.COMPLETED, UGCContentMission.Status.CANCELLED},
}


def _parse_due_at(value):
    value = str(value or "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        parsed = datetime.combine(parsed_date, time(hour=17)) if parsed_date else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _target_from_key(workspace, target_key):
    target_key = str(target_key or "").strip()
    if "::" not in target_key:
        return None
    target_type, target_id = target_key.split("::", 1)
    return find_catalog_target(workspace, target_type, target_id)


def _default_creator_prompt(*, target_label, deliverables, offer):
    offer_text = f" {offer.strip().rstrip('.')} is included." if offer.strip() else ""
    return (
        f"We’re looking for fresh community content from {target_label}: "
        f"{deliverables.strip().rstrip('.')}.{offer_text} Tag us or send us your post so we can feature it with credit."
    )[:5000]


@login_required
@require_permission("manage_workspace_settings")
def content_missions(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    queue = str(request.GET.get("view") or "active").strip().lower()
    if queue not in {"all", *MISSION_FILTERS}:
        queue = "active"
    query = str(request.GET.get("q") or "").strip()[:120]
    missions = UGCContentMission.objects.for_workspace(workspace.id)
    if queue != "all":
        missions = missions.filter(status__in=MISSION_FILTERS[queue])
    if query:
        missions = missions.filter(
            Q(title__icontains=query)
            | Q(brief__icontains=query)
            | Q(deliverables__icontains=query)
            | Q(target_label__icontains=query)
        )
    missions = missions.order_by("due_at", "-updated_at")
    page = Paginator(missions, MISSION_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    community_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    for mission in page.object_list:
        decorate_content_mission(mission)
        mission.review_url = f"{community_url}?{urlencode({'tab': 'discovered', 'q': mission.target_label})}"
        mission.ready_url = (
            f"{community_url}?{urlencode({'tab': 'approved', 'draft_state': 'ready', 'q': mission.target_label})}"
        )

    all_missions = UGCContentMission.objects.for_workspace(workspace.id)
    counts = {
        "all": all_missions.count(),
        **{name: all_missions.filter(status__in=statuses).count() for name, statuses in MISSION_FILTERS.items()},
    }
    prefill_type = str(request.GET.get("target_type") or "").strip()[:100]
    prefill_id = str(request.GET.get("target_id") or "").strip()[:255]
    prefill_target = find_catalog_target(workspace, prefill_type, prefill_id) if prefill_type and prefill_id else None
    prefill_key = prefill_target["picker_value"] if prefill_target else ""
    choices = target_choices(workspace, limit=120)
    if prefill_target and all(item["picker_value"] != prefill_key for item in choices):
        choices.insert(0, prefill_target)
    return render(
        request,
        "ugc/content_missions.html",
        {
            "workspace": workspace,
            "missions": page.object_list,
            "mission_page": page,
            "mission_queue": queue,
            "mission_query": query,
            "mission_counts": counts,
            "mission_target_choices": choices,
            "mission_prefill_target_key": prefill_key,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_content_mission(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:content_missions", kwargs={"workspace_id": workspace.id})
    target = _target_from_key(workspace, request.POST.get("target_key"))
    title = str(request.POST.get("title") or "").strip()[:255]
    deliverables = str(request.POST.get("deliverables") or "").strip()[:5000]
    if target is None:
        messages.error(request, "Choose a TN Game target from the existing target catalog.")
        return redirect(fallback)
    if not title or not deliverables:
        messages.error(request, "Add a mission title and what content you need.")
        return redirect(fallback)
    try:
        goal_count = int(request.POST.get("goal_count") or 3)
    except (TypeError, ValueError):
        goal_count = 0
    if not 1 <= goal_count <= 100:
        messages.error(request, "Set a ready-content goal between 1 and 100.")
        return redirect(fallback)
    due_at = _parse_due_at(request.POST.get("due_at"))
    if request.POST.get("due_at") and due_at is None:
        messages.error(request, "Enter a valid mission due date.")
        return redirect(fallback)
    offer = str(request.POST.get("offer") or "").strip()[:500]
    creator_prompt = str(request.POST.get("creator_prompt") or "").strip()[:5000]
    if not creator_prompt:
        creator_prompt = _default_creator_prompt(
            target_label=target["target_label"],
            deliverables=deliverables,
            offer=offer,
        )
    launch = str(request.POST.get("action") or "").strip().lower() == "launch"
    mission = UGCContentMission.objects.create(
        workspace=workspace,
        title=title,
        brief=str(request.POST.get("brief") or "").strip()[:5000],
        deliverables=deliverables,
        creator_prompt=creator_prompt,
        offer=offer,
        target_type=target["target_type"],
        target_id=target["target_id"],
        target_label=target["target_label"],
        target_url=target.get("target_url") or "",
        goal_count=goal_count,
        status=UGCContentMission.Status.ACTIVE if launch else UGCContentMission.Status.DRAFT,
        due_at=due_at,
        created_by=request.user,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.content_mission_created",
        target=mission,
        metadata={
            "status": mission.status,
            "target_type": mission.target_type,
            "target_id": mission.target_id,
            "goal_count": mission.goal_count,
            "due_at": mission.due_at.isoformat() if mission.due_at else "",
        },
        request=request,
    )
    messages.success(request, "Community mission launched." if launch else "Community mission saved as a draft.")
    return redirect(f"{fallback}?view={'active' if launch else 'planning'}")


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_content_mission(request, workspace_id, mission_id):
    workspace = _get_workspace(request, workspace_id)
    mission = get_object_or_404(UGCContentMission.objects.for_workspace(workspace.id), id=mission_id)
    fallback = reverse("ugc:content_missions", kwargs={"workspace_id": workspace.id})
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    action = str(request.POST.get("action") or "").strip().lower()
    transitions = {
        "launch": ({UGCContentMission.Status.DRAFT, UGCContentMission.Status.PAUSED}, UGCContentMission.Status.ACTIVE),
        "pause": ({UGCContentMission.Status.ACTIVE}, UGCContentMission.Status.PAUSED),
        "complete": (
            {UGCContentMission.Status.ACTIVE, UGCContentMission.Status.PAUSED},
            UGCContentMission.Status.COMPLETED,
        ),
        "reopen": (
            {UGCContentMission.Status.COMPLETED, UGCContentMission.Status.CANCELLED},
            UGCContentMission.Status.ACTIVE,
        ),
        "cancel": (
            {UGCContentMission.Status.DRAFT, UGCContentMission.Status.ACTIVE, UGCContentMission.Status.PAUSED},
            UGCContentMission.Status.CANCELLED,
        ),
    }
    transition = transitions.get(action)
    if transition is None or mission.status not in transition[0]:
        messages.error(request, "That mission action is no longer available.")
        return redirect(return_to)
    before = mission.status
    mission.status = transition[1]
    mission.completed_at = timezone.now() if mission.status == UGCContentMission.Status.COMPLETED else None
    mission.save(update_fields=["status", "completed_at", "updated_at"])
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=f"ugc.content_mission_{action}",
        target=mission,
        metadata={"before_status": before, "after_status": mission.status},
        request=request,
    )
    messages.success(request, f"Mission {mission.get_status_display().lower()}.")
    return redirect(return_to)
