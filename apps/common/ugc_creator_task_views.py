"""Server-rendered relationship task queue and task actions."""

from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCCreator, UGCCreatorIdentity, UGCCreatorTask
from .ugc_creator_views import _decorate_creator, _get_workspace, _safe_local_path

TASK_PAGE_SIZE = 12
VALID_TASK_FILTERS = {"today", "overdue", "upcoming", "open", "done"}
CONTACT_TASK_KINDS = {
    UGCCreatorTask.Kind.OUTREACH,
    UGCCreatorTask.Kind.FOLLOW_UP,
    UGCCreatorTask.Kind.THANK_YOU,
    UGCCreatorTask.Kind.COLLABORATION,
}


def _day_boundaries():
    current_date = timezone.localdate()
    current_timezone = timezone.get_current_timezone()
    today_start = timezone.make_aware(datetime.combine(current_date, time.min), current_timezone)
    tomorrow_start = timezone.make_aware(
        datetime.combine(current_date + timedelta(days=1), time.min),
        current_timezone,
    )
    return today_start, tomorrow_start


def _parse_due_at(value):
    value = str(value or "").strip()
    if not value:
        tomorrow = timezone.localdate() + timedelta(days=1)
        parsed = datetime.combine(tomorrow, time(hour=9))
    else:
        parsed = parse_datetime(value)
        if parsed is None:
            parsed_date = parse_date(value)
            parsed = datetime.combine(parsed_date, time(hour=9)) if parsed_date else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@login_required
@require_permission("manage_workspace_settings")
def creator_tasks(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    task_filter = str(request.GET.get("view") or "today").strip().lower()
    if task_filter not in VALID_TASK_FILTERS:
        task_filter = "today"
    query = str(request.GET.get("q") or "").strip()[:120]
    today_start, tomorrow_start = _day_boundaries()

    identities = UGCCreatorIdentity.objects.order_by("-is_primary", "platform", "normalized_handle")
    tasks = (
        UGCCreatorTask.objects.for_workspace(workspace.id)
        .select_related("creator", "submission", "collaboration")
        .prefetch_related(Prefetch("creator__identities", queryset=identities))
    )
    if task_filter == "today":
        tasks = tasks.filter(status=UGCCreatorTask.Status.OPEN, due_at__lt=tomorrow_start)
    elif task_filter == "overdue":
        tasks = tasks.filter(status=UGCCreatorTask.Status.OPEN, due_at__lt=today_start)
    elif task_filter == "upcoming":
        tasks = tasks.filter(status=UGCCreatorTask.Status.OPEN, due_at__gte=tomorrow_start)
    elif task_filter == "done":
        tasks = tasks.filter(status=UGCCreatorTask.Status.DONE)
    else:
        tasks = tasks.filter(status=UGCCreatorTask.Status.OPEN)
    if query:
        tasks = tasks.filter(
            Q(title__icontains=query)
            | Q(note__icontains=query)
            | Q(collaboration__title__icontains=query)
            | Q(creator__display_name__icontains=query)
            | Q(creator__identities__handle__icontains=query)
        ).distinct()
    tasks = tasks.order_by("-completed_at", "-updated_at") if task_filter == "done" else tasks.order_by("due_at")

    paginator = Paginator(tasks, TASK_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)
    decorated_ids = set()
    for task in page.object_list:
        if task.creator_id not in decorated_ids:
            _decorate_creator(task.creator)
            decorated_ids.add(task.creator_id)

    all_tasks = UGCCreatorTask.objects.for_workspace(workspace.id)
    return render(
        request,
        "ugc/creator_tasks.html",
        {
            "workspace": workspace,
            "creator_tasks": page.object_list,
            "creator_task_page": page,
            "creator_task_filter": task_filter,
            "creator_task_query": query,
            "creator_task_counts": {
                "today": all_tasks.filter(status=UGCCreatorTask.Status.OPEN, due_at__lt=tomorrow_start).count(),
                "overdue": all_tasks.filter(status=UGCCreatorTask.Status.OPEN, due_at__lt=today_start).count(),
                "upcoming": all_tasks.filter(
                    status=UGCCreatorTask.Status.OPEN,
                    due_at__gte=tomorrow_start,
                ).count(),
                "open": all_tasks.filter(status=UGCCreatorTask.Status.OPEN).count(),
                "done": all_tasks.filter(status=UGCCreatorTask.Status.DONE).count(),
            },
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_creator_task(request, workspace_id, creator_id):
    workspace = _get_workspace(request, workspace_id)
    creator = get_object_or_404(UGCCreator.objects.for_workspace(workspace.id), id=creator_id)
    fallback = reverse("ugc:creator_detail", kwargs={"workspace_id": workspace.id, "creator_id": creator.id})
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    kind = str(request.POST.get("kind") or UGCCreatorTask.Kind.CUSTOM).strip().lower()
    if kind not in {value for value, _label in UGCCreatorTask.Kind.choices}:
        messages.error(request, "Choose a valid relationship task type.")
        return redirect(return_to)
    if creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT and kind in CONTACT_TASK_KINDS:
        messages.error(
            request, "This creator is marked Do not contact. Choose a non-contact task or update the relationship."
        )
        return redirect(return_to)
    due_at = _parse_due_at(request.POST.get("due_at"))
    if due_at is None:
        messages.error(request, "Enter a valid task due date.")
        return redirect(return_to)

    default_title = dict(UGCCreatorTask.Kind.choices)[kind]
    task = UGCCreatorTask.objects.create(
        workspace=workspace,
        creator=creator,
        kind=kind,
        title=str(request.POST.get("title") or default_title).strip()[:255] or default_title,
        note=str(request.POST.get("note") or "").strip()[:5000],
        due_at=due_at,
        created_by=request.user,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_task_created",
        target=creator,
        metadata={"task_id": str(task.id), "kind": task.kind, "due_at": task.due_at.isoformat()},
        request=request,
    )
    messages.success(request, "Relationship task added.")
    return redirect(return_to)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_creator_task(request, workspace_id, task_id):
    workspace = _get_workspace(request, workspace_id)
    task = get_object_or_404(
        UGCCreatorTask.objects.for_workspace(workspace.id).select_related("creator"),
        id=task_id,
    )
    fallback = reverse("ugc:creator_tasks", kwargs={"workspace_id": workspace.id})
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    action = str(request.POST.get("action") or "").strip().lower()
    before = {"status": task.status, "due_at": task.due_at.isoformat()}
    now = timezone.now()

    if action == "complete" and task.status == UGCCreatorTask.Status.OPEN:
        task.status = UGCCreatorTask.Status.DONE
        task.completed_at = now
        task.completed_by = request.user
        message = "Relationship task completed."
    elif action == "dismiss" and task.status == UGCCreatorTask.Status.OPEN:
        task.status = UGCCreatorTask.Status.DISMISSED
        task.completed_at = now
        task.completed_by = request.user
        message = "Relationship task dismissed."
    elif action == "reopen" and task.status in {UGCCreatorTask.Status.DONE, UGCCreatorTask.Status.DISMISSED}:
        task.status = UGCCreatorTask.Status.OPEN
        task.completed_at = None
        task.completed_by = None
        message = "Relationship task reopened."
    elif action.startswith("snooze_") and task.status == UGCCreatorTask.Status.OPEN:
        try:
            days = int(action.removeprefix("snooze_"))
        except ValueError:
            days = 0
        if days not in {1, 3, 7, 14}:
            messages.error(request, "Choose a valid snooze period.")
            return redirect(return_to)
        task.due_at = now + timedelta(days=days)
        message = f"Task snoozed for {days} day{'s' if days != 1 else ''}."
    else:
        messages.error(request, "That task action is no longer available.")
        return redirect(return_to)

    task.save(update_fields=["status", "due_at", "completed_at", "completed_by", "updated_at"])
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=f"ugc.creator_task_{action.split('_')[0]}",
        target=task.creator,
        metadata={
            "task_id": str(task.id),
            "kind": task.kind,
            "before": before,
            "after": {"status": task.status, "due_at": task.due_at.isoformat()},
        },
        request=request,
    )
    messages.success(request, message)
    return redirect(return_to)
