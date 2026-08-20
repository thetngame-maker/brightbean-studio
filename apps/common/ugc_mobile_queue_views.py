"""Lightweight phone moderation queue and focused review flow.

Desktop keeps using the original moderation view/template. Phones render a small,
server-paginated queue with minimal HTML and no enhancement bundle so Safari
never has to hydrate the desktop moderation dashboard.
"""

import math
from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.members.decorators import require_permission

from . import ugc_views
from .models import UGCReport, UGCSubmission
from .ugc_permissions import get_permission
from .ugc_provenance import get_provenance
from .ugc_relevance import score_relevance
from .ugc_views import (
    VALID_TABS,
    _discovered_q,
    _get_workspace,
    _pending_submission_q,
    _queue_counts,
)


MOBILE_PAGE_SIZE = 12
FOLLOWUP_AFTER_DAYS = 3
VALID_RELEVANCE = {"relevant", "all", "strong", "possible", "low"}
VALID_MEDIA = {"all", "reels", "photos"}
VALID_SORT = {"newest", "engaged", "liked", "viewed", "followup", "today"}
VALID_PERMISSION = {"all", "not_contacted", "requested", "followup_due", "today", "granted", "declined"}


def _is_mobile_request(request):
    ua = (request.META.get("HTTP_USER_AGENT") or "").lower()
    return any(token in ua for token in ("iphone", "ipod", "android", "mobile"))


def _positive_page(raw):
    try:
        return max(1, int(raw or 1))
    except (TypeError, ValueError):
        return 1


def _metric(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(value.replace(",", "").strip()))
        except (TypeError, ValueError):
            return 0
    return 0


def _as_datetime(value):
    if not value:
        return None
    if hasattr(value, "tzinfo"):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _permission_message(submission):
    handle = (submission.contributor_handle or "").strip().lstrip("@")
    title = (submission.title or submission.target_label or "your post").strip()
    greeting = f"Hi @{handle}!" if handle else "Hi!"
    credit = (
        f" We’ll credit you as @{handle} and link back to your original post."
        if handle
        else " We’ll credit you and link back to your original post."
    )
    return (
        f"{greeting} We came across your {title} post and would love to feature it on "
        f"The TN Game’s social media and website.{credit} If you’re okay with us sharing it, "
        "please reply YES to this message. Thank you!"
    )


def _followup_message(submission):
    handle = (submission.contributor_handle or "").strip().lstrip("@")
    greeting = f"Hi @{handle}!" if handle else "Hi!"
    return (
        f"{greeting} Just following up on our earlier message — we’d still love to feature your post "
        "on The TN Game’s social media and website, with full credit and a link back to your original post. "
        "If you’re okay with us sharing it, please reply YES. Thank you!"
    )


def _followup_state(metadata, permission):
    outreach = metadata.get("outreach") if isinstance(metadata.get("outreach"), dict) else {}
    requested_at = _as_datetime(outreach.get("requested_at") or permission.get("updated_at"))
    last_followup_at = _as_datetime(outreach.get("last_followup_at"))
    try:
        count = max(0, int(outreach.get("followup_count") or 0))
    except (TypeError, ValueError):
        count = 0

    anchor = last_followup_at or requested_at
    due_at = anchor + timedelta(days=FOLLOWUP_AFTER_DAYS) if anchor else None
    label = "Waiting reply"
    due = False
    overdue_days = 0
    if due_at:
        seconds = (due_at - timezone.now()).total_seconds()
        if seconds <= 0:
            due = True
            overdue_days = max(0, int(abs(seconds) // 86400))
            label = "Follow-up due" if overdue_days == 0 else f"Overdue by {overdue_days}d"
        else:
            days = max(1, math.ceil(seconds / 86400))
            label = "Due tomorrow" if days == 1 else f"Due in {days}d"

    return {
        "requested_at": requested_at,
        "last_followup_at": last_followup_at,
        "followup_count": count,
        "due_at": due_at,
        "due": due,
        "label": label,
        "overdue_days": overdue_days,
    }


def _decorate_submission(submission):
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    provenance = get_provenance(metadata)
    discovery = metadata.get("discovery_import") if isinstance(metadata.get("discovery_import"), dict) else {}

    relevance = score_relevance(
        {
            "caption": submission.body,
            "location_name": discovery.get("location_name"),
            "source_title": submission.title,
        },
        query=provenance.get("discovery_query", ""),
        target_label=submission.target_label,
    )
    submission.mobile_relevance_status = relevance["relevance_status"]
    submission.mobile_relevance_reason = relevance["relevance_reason"]
    submission.mobile_discovery_method = discovery.get("discovery_method") or ""
    submission.mobile_like_count = discovery.get("like_count")
    submission.mobile_comment_count = discovery.get("comment_count")
    submission.mobile_view_count = discovery.get("view_count")
    submission.mobile_thumbnail_url = discovery.get("thumbnail_url") or ""
    submission.mobile_source_url = provenance.get("source_url") or ""

    permission = get_permission(metadata)
    submission.mobile_permission_status = permission.get("status") or "not_contacted"
    submission.mobile_permission_message = _permission_message(submission)
    submission.mobile_followup_message = _followup_message(submission)
    followup = _followup_state(metadata, permission)
    submission.mobile_requested_at = followup["requested_at"]
    submission.mobile_last_followup_at = followup["last_followup_at"]
    submission.mobile_followup_count = followup["followup_count"]
    submission.mobile_followup_due_at = followup["due_at"]
    submission.mobile_followup_due = followup["due"]
    submission.mobile_followup_label = followup["label"]

    handle = (submission.contributor_handle or provenance.get("source_handle") or "").strip().lstrip("@")
    submission.mobile_creator_profile_url = f"https://www.instagram.com/{handle}/" if handle else submission.mobile_source_url

    stored_media_type = str(discovery.get("media_type") or "").strip().lower()
    if submission.media_asset and submission.media_asset.is_video:
        submission.mobile_media_type = "video"
    elif submission.media_asset and submission.media_asset.is_image:
        submission.mobile_media_type = "image"
    elif stored_media_type in {"video", "image"}:
        submission.mobile_media_type = stored_media_type
    else:
        submission.mobile_media_type = ""

    likes = _metric(submission.mobile_like_count)
    comments = _metric(submission.mobile_comment_count)
    views = _metric(submission.mobile_view_count)
    submission.mobile_engagement_score = likes + (comments * 10) + (views / 100)
    return submission


def _is_top_prospect(submission):
    return (
        getattr(submission, "mobile_relevance_status", "possible") == "strong"
        and getattr(submission, "mobile_media_type", "") == "video"
        and getattr(submission, "mobile_permission_status", "not_contacted") == "not_contacted"
    )


def _is_followup_due(submission):
    return (
        getattr(submission, "mobile_permission_status", "not_contacted") == "requested"
        and bool(getattr(submission, "mobile_followup_due", False))
    )


def _workflow_counts(submissions):
    counts = {
        "today": 0,
        "top_prospects": 0,
        "needs_outreach": 0,
        "waiting_reply": 0,
        "followup_due": 0,
        "granted": 0,
        "low_relevance": 0,
    }
    for item in submissions:
        relevance = getattr(item, "mobile_relevance_status", "possible")
        permission = getattr(item, "mobile_permission_status", "not_contacted")
        top = _is_top_prospect(item)
        due = _is_followup_due(item)
        if top:
            counts["top_prospects"] += 1
        if relevance != "low" and permission == "not_contacted":
            counts["needs_outreach"] += 1
        if permission == "requested":
            counts["waiting_reply"] += 1
        if due:
            counts["followup_due"] += 1
        if relevance != "low" and permission == "granted":
            counts["granted"] += 1
        if relevance == "low":
            counts["low_relevance"] += 1
        if top or due:
            counts["today"] += 1
    return counts


def _matches_relevance(submission, relevance_filter):
    status = getattr(submission, "mobile_relevance_status", "possible")
    if relevance_filter == "all":
        return True
    if relevance_filter == "relevant":
        return status != "low"
    return status == relevance_filter


def _matches_media(submission, media_filter):
    if media_filter == "all":
        return True
    media_type = getattr(submission, "mobile_media_type", "")
    if media_filter == "reels":
        return media_type == "video"
    if media_filter == "photos":
        return media_type == "image"
    return True


def _matches_permission(submission, permission_filter):
    if permission_filter == "all":
        return True
    if permission_filter == "followup_due":
        return _is_followup_due(submission)
    if permission_filter == "today":
        return _is_followup_due(submission) or _is_top_prospect(submission)
    return getattr(submission, "mobile_permission_status", "not_contacted") == permission_filter


def _sort_mobile(submissions, sort_mode):
    if sort_mode == "liked":
        return sorted(submissions, key=lambda item: (_metric(getattr(item, "mobile_like_count", 0)), item.submitted_at), reverse=True)
    if sort_mode == "viewed":
        return sorted(submissions, key=lambda item: (_metric(getattr(item, "mobile_view_count", 0)), item.submitted_at), reverse=True)
    if sort_mode == "engaged":
        return sorted(submissions, key=lambda item: (getattr(item, "mobile_engagement_score", 0), item.submitted_at), reverse=True)
    if sort_mode == "followup":
        return sorted(
            submissions,
            key=lambda item: (
                0 if getattr(item, "mobile_followup_due", False) else 1,
                getattr(item, "mobile_followup_due_at", None).timestamp()
                if getattr(item, "mobile_followup_due_at", None)
                else float("inf"),
            ),
        )
    if sort_mode == "today":
        return sorted(
            submissions,
            key=lambda item: (
                0 if _is_followup_due(item) else 1,
                (
                    getattr(item, "mobile_followup_due_at", None).timestamp()
                    if _is_followup_due(item) and getattr(item, "mobile_followup_due_at", None)
                    else -float(getattr(item, "mobile_engagement_score", 0) or 0)
                ),
                -item.submitted_at.timestamp(),
            ),
        )
    return sorted(submissions, key=lambda item: item.submitted_at, reverse=True)


def _filters_from_request(request):
    relevance = (request.GET.get("relevance") or "relevant").strip().lower()
    if relevance not in VALID_RELEVANCE:
        relevance = "relevant"
    media = (request.GET.get("media") or "all").strip().lower()
    if media not in VALID_MEDIA:
        media = "all"
    sort_mode = (request.GET.get("sort") or "newest").strip().lower()
    if sort_mode not in VALID_SORT:
        sort_mode = "newest"
    permission = (request.GET.get("permission") or "all").strip().lower()
    if permission not in VALID_PERMISSION:
        permission = "all"
    return relevance, media, sort_mode, permission


def _filtered_queue(request, workspace, tab):
    qs = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .select_related("contributor", "media_asset", "moderated_by")
        .annotate(
            open_report_count=Count(
                "reports",
                filter=Q(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]),
                distinct=True,
            )
        )
    )
    if tab == "discovered":
        qs = qs.filter(status=UGCSubmission.Status.PENDING).filter(_discovered_q())
    elif tab == "pending":
        qs = qs.filter(status=UGCSubmission.Status.PENDING).filter(_pending_submission_q())
    elif tab == "approved":
        qs = qs.filter(status=UGCSubmission.Status.APPROVED)
    elif tab == "reported":
        qs = qs.filter(reports__status__in=[UGCReport.Status.OPEN, UGCReport.Status.REVIEWING]).distinct()
    elif tab == "removed":
        qs = qs.filter(status=UGCSubmission.Status.REMOVED)

    kind = request.GET.get("kind", "").strip()
    if kind in dict(UGCSubmission.Kind.choices):
        qs = qs.filter(kind=kind)
    else:
        kind = ""

    search_query = (request.GET.get("q") or "").strip()[:120]
    if search_query:
        qs = qs.filter(
            Q(title__icontains=search_query)
            | Q(body__icontains=search_query)
            | Q(contributor_handle__icontains=search_query)
            | Q(contributor_name__icontains=search_query)
            | Q(target_label__icontains=search_query)
        )

    relevance, media, sort_mode, permission = _filters_from_request(request)
    decorated = [_decorate_submission(submission) for submission in qs[:500]]
    workflow_counts = _workflow_counts(decorated) if tab == "discovered" else {}
    if tab == "discovered":
        decorated = [item for item in decorated if _matches_relevance(item, relevance)]
        decorated = [item for item in decorated if _matches_permission(item, permission)]
    decorated = [item for item in decorated if _matches_media(item, media)]
    decorated = _sort_mobile(decorated, sort_mode)
    return decorated, {
        "kind": kind,
        "search": search_query,
        "relevance": relevance,
        "media": media,
        "sort": sort_mode,
        "permission": permission,
    }, workflow_counts


def _queue_query(params, *, page=None, list_mode=False):
    values = {
        "tab": params.get("tab", "discovered"),
        "relevance": params.get("relevance", "relevant"),
        "media": params.get("media", "all"),
        "sort": params.get("sort", "newest"),
        "permission": params.get("permission", "all"),
    }
    if params.get("kind"):
        values["kind"] = params["kind"]
    if params.get("search"):
        values["q"] = params["search"]
    if page:
        values["page"] = page
    if list_mode:
        values["list"] = "1"
    return urlencode(values)


def _review_url(workspace, submission, params, return_to):
    url = reverse("ugc:mobile_review", kwargs={"workspace_id": workspace.id, "submission_id": submission.id})
    query = _queue_query(params)
    encoded_return = urlencode({"x": return_to})[2:]
    return f"{url}?{query}&return_to={encoded_return}"


@login_required
@require_permission("manage_workspace_settings")
def moderation_queue(request, workspace_id):
    if not _is_mobile_request(request):
        return ugc_views.moderation_queue(request, workspace_id)

    workspace = _get_workspace(request, workspace_id)
    tab = request.GET.get("tab", "pending")
    if tab not in VALID_TABS:
        tab = "pending"

    decorated, filters, workflow_counts = _filtered_queue(request, workspace, tab)
    params = {"tab": tab, **filters}
    queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    today_session = tab == "discovered" and filters["permission"] == "today"

    # Today is an action-oriented queue: tapping its quick view starts focused review.
    # `list=1` is reserved for Done/back navigation so it never loops back into review.
    if today_session and decorated and request.GET.get("list") != "1" and not request.GET.get("page"):
        return_to = f"{queue_url}?{_queue_query(params, list_mode=True)}"
        return redirect(_review_url(workspace, decorated[0], params, return_to))

    total_items = len(decorated)
    total_pages = max(1, (total_items + MOBILE_PAGE_SIZE - 1) // MOBILE_PAGE_SIZE)
    page = min(_positive_page(request.GET.get("page")), total_pages)
    start = (page - 1) * MOBILE_PAGE_SIZE
    submissions = decorated[start : start + MOBILE_PAGE_SIZE]
    current_return_to = f"{queue_url}?{_queue_query(params, page=page if page > 1 else None, list_mode=today_session)}"
    start_review_url = _review_url(workspace, decorated[0], params, current_return_to) if today_session and decorated else ""

    context = {
        "workspace": workspace,
        "submissions": submissions,
        "active_tab": tab,
        "active_kind": filters["kind"],
        "kind_choices": UGCSubmission.Kind.choices,
        "queue_counts": _queue_counts(workspace),
        "ugc_mobile_workflow_counts": workflow_counts,
        "ugc_mobile_page": page,
        "ugc_mobile_total_items": total_items,
        "ugc_mobile_total_pages": total_pages,
        "ugc_mobile_prev_page": page - 1 if page > 1 else None,
        "ugc_mobile_next_page": page + 1 if page < total_pages else None,
        "ugc_mobile_search": filters["search"],
        "ugc_mobile_relevance": filters["relevance"],
        "ugc_mobile_media": filters["media"],
        "ugc_mobile_sort": filters["sort"],
        "ugc_mobile_permission": filters["permission"],
        "ugc_mobile_today_session": today_session,
        "ugc_mobile_start_review_url": start_review_url,
    }
    response = render(request, "ugc/moderation_queue_mobile.html", context)
    response["X-UGC-Mobile-Lite"] = "1"
    response["X-UGC-Mobile-Page"] = str(page)
    return response


@login_required
@require_permission("manage_workspace_settings")
def mobile_review(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    tab = request.GET.get("tab", "discovered")
    if tab not in VALID_TABS:
        tab = "discovered"

    decorated, filters, workflow_counts = _filtered_queue(request, workspace, tab)
    index = next((i for i, item in enumerate(decorated) if item.id == submission_id), None)
    if index is None:
        raise Http404("Community item is not in this filtered review queue.")

    submission = decorated[index]
    params = {"tab": tab, **filters}
    queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    today_session = tab == "discovered" and filters["permission"] == "today"
    default_return = f"{queue_url}?{_queue_query(params, list_mode=today_session)}"
    return_to = request.GET.get("return_to") or default_return
    if not return_to.startswith("/"):
        return_to = default_return

    prev_item = decorated[index - 1] if index > 0 else None
    next_item = decorated[index + 1] if index + 1 < len(decorated) else None
    review_query = _queue_query(params)

    if next_item:
        action_return_to = _review_url(workspace, next_item, params, return_to)
    else:
        action_return_to = return_to

    context = {
        "workspace": workspace,
        "submission": submission,
        "active_tab": tab,
        "queue_counts": _queue_counts(workspace),
        "ugc_mobile_workflow_counts": workflow_counts,
        "review_index": index + 1,
        "review_total": len(decorated),
        "review_remaining": max(0, len(decorated) - index),
        "review_session_label": "Today" if today_session else "Review",
        "review_is_today": today_session,
        "review_prev": prev_item,
        "review_next": next_item,
        "review_query": review_query,
        "review_return_to": return_to,
        "review_action_return_to": action_return_to,
    }
    response = render(request, "ugc/moderation_review_mobile.html", context)
    response["X-UGC-Mobile-Review"] = "1"
    return response
