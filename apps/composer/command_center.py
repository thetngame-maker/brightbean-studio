"""A five-minute, read-only briefing assembled from Studio's real queues."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db.models import Q
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from apps.common.models import UGCContentMission, UGCCreatorTask, UGCSubmission
from apps.common.ugc_mobile_queue_views import discovered_workflow_snapshot
from apps.common.ugc_views import _pending_submission_q
from apps.inbox.models import InboxMessage

from .models import PlatformPost
from .orchestration import tight_rollout_count

RUN_MINUTES = 5


def _day_bounds(workspace):
    try:
        local_timezone = ZoneInfo(workspace.effective_timezone or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        local_timezone = ZoneInfo("UTC")
    local_now = timezone.now().astimezone(local_timezone)
    start = datetime.combine(local_now.date(), time.min, tzinfo=local_timezone)
    return local_now, start, start + timedelta(days=1)


def _url(name, workspace, **kwargs):
    return reverse(name, kwargs={"workspace_id": workspace.id, **kwargs})


def _add(actions, *, kind, title, detail, url, minutes=1, tone="purple", count=1, task=None):
    actions.append(
        {
            "kind": kind,
            "title": title,
            "detail": detail,
            "url": url,
            "minutes": minutes,
            "tone": tone,
            "count": count,
            "task": task,
        }
    )


def _five_minute_run(actions):
    selected = []
    minutes = 0
    for action in actions:
        if selected and minutes + action["minutes"] > RUN_MINUTES:
            continue
        selected.append(action)
        minutes += action["minutes"]
        if minutes >= RUN_MINUTES:
            break
    return selected, minutes


def build_command_center(workspace, *, permissions=None):
    """Rank current work without copying or mutating any underlying workflow."""
    permissions = permissions or {}
    local_now, today_start, tomorrow_start = _day_bounds(workspace)
    actions = []

    failed_queryset = PlatformPost.objects.filter(post__workspace=workspace, status=PlatformPost.Status.FAILED)
    failed_count = failed_queryset.count()
    failed = list(failed_queryset.select_related("post", "social_account").order_by("-updated_at")[:3])
    for variant in failed:
        title = variant.post.title or variant.post.caption[:80] or "Untitled post"
        _add(
            actions,
            kind="Publishing",
            title=f"Fix failed post: {title}",
            detail=(variant.publish_error[:140] or "Publishing failed") + f" · {variant.social_account.display_label}",
            url=f"{_url('composer:compose_edit', workspace, post_id=variant.post_id)}?account={variant.social_account_id}",
            minutes=2,
            tone="red",
        )

    pending_review = []
    pending_review_count = 0
    if permissions.get("approve_posts", False):
        pending_review_queryset = PlatformPost.objects.filter(
            post__workspace=workspace,
            status=PlatformPost.Status.PENDING_REVIEW,
        )
        pending_review_count = pending_review_queryset.values("post_id").distinct().count()
        pending_review = list(
            pending_review_queryset.select_related("post", "social_account").order_by("updated_at")[:6]
        )
        seen_posts = set()
        for variant in pending_review:
            if variant.post_id in seen_posts:
                continue
            seen_posts.add(variant.post_id)
            title = variant.post.title or variant.post.caption[:80] or "Untitled post"
            _add(
                actions,
                kind="Approval",
                title=f"Review: {title}",
                detail=f"Waiting for internal approval · {variant.social_account.display_label}",
                url=f"{_url('calendar:calendar', workspace)}?tab=approvals&mode=list&approval_status=pending_review",
                tone="orange",
            )

    discovered = discovered_workflow_snapshot(workspace)
    today_count = discovered["counts"]["today"]
    if today_count:
        community_today_url = (
            _url("ugc:moderation_queue", workspace)
            + "?"
            + urlencode({"tab": "discovered", "permission": "today", "sort": "today"})
        )
        due_count = discovered["counts"]["followup_due"]
        prospect_count = discovered["counts"]["top_prospects"]
        detail_bits = []
        if due_count:
            detail_bits.append(f"{due_count} follow-up{'s' if due_count != 1 else ''} due")
        if prospect_count:
            detail_bits.append(f"{prospect_count} strong Reel prospect{'s' if prospect_count != 1 else ''}")
        _add(
            actions,
            kind="Community",
            title=f"Run Community Today · {today_count}",
            detail=" · ".join(detail_bits),
            url=community_today_url,
            minutes=2,
            tone="green",
            count=today_count,
        )

    creator_task_queryset = UGCCreatorTask.objects.for_workspace(workspace.id).filter(
        status=UGCCreatorTask.Status.OPEN,
        due_at__lt=tomorrow_start,
    )
    creator_task_count = creator_task_queryset.count()
    creator_tasks = list(creator_task_queryset.select_related("creator").order_by("due_at")[:4])
    task_queue_url = _url("ugc:creator_tasks", workspace) + "?view=today"
    for task in creator_tasks:
        creator_label = task.creator.display_name or task.creator.preferred_credit or "Creator"
        due_label = "Overdue" if task.due_at < timezone.now() else "Due today"
        _add(
            actions,
            kind="Creator",
            title=task.title,
            detail=f"{creator_label} · {due_label}",
            url=task_queue_url,
            tone="blue",
            task=task,
        )

    unread_inbox = 0
    if permissions.get("use_inbox", False):
        unread_inbox = (
            InboxMessage.objects.for_workspace(workspace.id).filter(status=InboxMessage.Status.UNREAD).count()
        )
        if unread_inbox:
            _add(
                actions,
                kind="Inbox",
                title=f"Reply to {unread_inbox} unread message{'s' if unread_inbox != 1 else ''}",
                detail="Comments, mentions, and direct messages waiting in Social Inbox",
                url=_url("inbox:feed", workspace),
                minutes=2,
                tone="blue",
                count=unread_inbox,
            )

    pending_ugc = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(Q(status=UGCSubmission.Status.PENDING) & _pending_submission_q())
        .count()
    )
    if pending_ugc:
        _add(
            actions,
            kind="Moderation",
            title=f"Review {pending_ugc} pending community item{'s' if pending_ugc != 1 else ''}",
            detail="Permission-safe content waiting for approval",
            url=_url("ugc:moderation_queue", workspace) + "?tab=pending",
            minutes=2,
            tone="green",
            count=pending_ugc,
        )

    spacing_count = tight_rollout_count(workspace)
    if spacing_count:
        _add(
            actions,
            kind="Orchestration",
            title=f"Space {spacing_count} account rollout{'s' if spacing_count != 1 else ''}",
            detail="Scheduled account variants are launching less than 30 minutes apart",
            url=_url("composer:orchestration", workspace) + "?view=spacing",
            tone="orange",
            count=spacing_count,
        )

    due_mission_queryset = UGCContentMission.objects.for_workspace(workspace.id).filter(
        status=UGCContentMission.Status.ACTIVE,
        due_at__isnull=False,
        due_at__lt=tomorrow_start,
    )
    due_mission_count = due_mission_queryset.count()
    due_missions = list(due_mission_queryset.order_by("due_at")[:2])
    for mission in due_missions:
        _add(
            actions,
            kind="Mission",
            title=mission.title,
            detail=f"{mission.target_label} · {'Overdue' if mission.due_at < timezone.now() else 'Due today'}",
            url=_url("ugc:content_missions", workspace) + "?view=active",
            tone="purple",
        )

    run, run_minutes = _five_minute_run(actions)
    scheduled_today = (
        PlatformPost.objects.filter(post__workspace=workspace, status=PlatformPost.Status.SCHEDULED)
        .annotate(effective_at=Coalesce("scheduled_at", "post__scheduled_at"))
        .filter(effective_at__gte=today_start, effective_at__lt=tomorrow_start)
        .count()
    )
    published_today = PlatformPost.objects.filter(
        post__workspace=workspace,
        status=PlatformPost.Status.PUBLISHED,
        published_at__gte=today_start,
        published_at__lt=tomorrow_start,
    ).count()
    return {
        "local_now": local_now,
        "actions": actions,
        "run": run,
        "run_minutes": run_minutes,
        "remaining_actions": max(0, len(actions) - len(run)),
        "counts": {
            "failed": failed_count,
            "approvals": pending_review_count,
            "community_today": today_count,
            "creator_today": creator_task_count,
            "inbox_unread": unread_inbox,
            "pending_ugc": pending_ugc,
            "spacing": spacing_count,
            "missions_due": due_mission_count,
            "scheduled_today": scheduled_today,
            "published_today": published_today,
        },
        "links": {
            "community": _url("ugc:moderation_queue", workspace) + "?tab=discovered&permission=today&sort=today",
            "approvals": _url("calendar:calendar", workspace) + "?tab=approvals&mode=list",
            "inbox": _url("inbox:feed", workspace),
            "orchestration": _url("composer:orchestration", workspace),
            "tasks": task_queue_url,
            "missions": _url("ugc:content_missions", workspace),
        },
    }
