"""Server-rendered Performance Learning Loop for published Studio posts."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.composer.models import PlatformPost
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import ContentPerformanceProfile
from .ugc_creator_views import _safe_local_path
from .ugc_performance_learning import build_performance_learning, inferred_ugc_for_post
from .ugc_target_catalog import find_catalog_target, target_choices
from .ugc_views import _get_workspace

LEARNING_PAGE_SIZE = 12
RANGE_CHOICES = {30, 90, 180, 365}
VALID_QUEUES = {"all", "needs_tags", "tagged", "ugc"}


def _range_days(value):
    if str(value or "").lower() == "all":
        return None
    try:
        days = int(value or 90)
    except (TypeError, ValueError):
        return 90
    return days if days in RANGE_CHOICES else 90


def _valid_choice(value, choices, *, allow_blank=True):
    value = str(value or "").strip().lower()
    valid = {key for key, _label in choices}
    if (not value and allow_blank) or value in valid:
        return value
    return None


@login_required
@require_permission("manage_workspace_settings")
def performance_learning(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    days = _range_days(request.GET.get("range"))
    learning = build_performance_learning(workspace, days=days)
    queue = str(request.GET.get("view") or "all").strip().lower()
    if queue not in VALID_QUEUES:
        queue = "all"
    query = str(request.GET.get("q") or "").strip()[:120]
    account_id = str(request.GET.get("account") or "").strip()
    rows = learning["rows"]
    if queue == "needs_tags":
        rows = [row for row in rows if row["needs_tags"]]
    elif queue == "tagged":
        rows = [row for row in rows if not row["needs_tags"]]
    elif queue == "ugc":
        rows = [row for row in rows if row["source_type"] == ContentPerformanceProfile.SourceType.UGC]
    if account_id:
        rows = [row for row in rows if str(row["account_id"]) == account_id]
    if query:
        lowered = query.lower()
        rows = [
            row
            for row in rows
            if lowered in (row["post"].title or "").lower()
            or lowered in (row["caption"] or "").lower()
            or lowered in row["account_label"].lower()
            or lowered in row["target_label"].lower()
            or lowered in row["creator_label"].lower()
        ]
    page = Paginator(rows, LEARNING_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    choices = target_choices(workspace, limit=120)
    choice_keys = {item["picker_value"] for item in choices}
    for row in page.object_list:
        row["target_key"] = (
            f"{row['target_type']}::{row['target_id']}" if row["target_type"] and row["target_id"] else ""
        )
        row["target_missing_from_choices"] = bool(row["target_key"] and row["target_key"] not in choice_keys)
    accounts = []
    seen_accounts = set()
    for row in learning["rows"]:
        if row["account_id"] in seen_accounts:
            continue
        seen_accounts.add(row["account_id"])
        accounts.append({"id": str(row["account_id"]), "label": row["account_label"], "platform": row["platform"]})
    return render(
        request,
        "ugc/performance_learning.html",
        {
            "workspace": workspace,
            "learning_rows": page.object_list,
            "learning_page": page,
            "learning_lessons": learning["lessons"],
            "learning_counts": learning["counts"],
            "learning_limit_reached": learning["limit_reached"],
            "learning_queue": queue,
            "learning_query": query,
            "learning_account_id": account_id,
            "learning_accounts": accounts,
            "learning_days": days,
            "learning_range": "all" if days is None else str(days),
            "learning_target_choices": choices,
            "source_type_choices": ContentPerformanceProfile.SourceType.choices,
            "opening_hook_choices": ContentPerformanceProfile.OpeningHook.choices,
            "caption_style_choices": ContentPerformanceProfile.CaptionStyle.choices,
            "season_choices": ContentPerformanceProfile.Season.choices,
            "subject_choices": ContentPerformanceProfile.Subject.choices,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_performance_profile(request, workspace_id, platform_post_id):
    workspace = _get_workspace(request, workspace_id)
    platform_post = get_object_or_404(
        PlatformPost.objects.select_related("post", "social_account"),
        id=platform_post_id,
        social_account__workspace=workspace,
        status=PlatformPost.Status.PUBLISHED,
    )
    fallback = reverse("ugc:performance_learning", kwargs={"workspace_id": workspace.id})
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    source_type = _valid_choice(
        request.POST.get("source_type"), ContentPerformanceProfile.SourceType.choices, allow_blank=False
    )
    opening_hook = _valid_choice(request.POST.get("opening_hook"), ContentPerformanceProfile.OpeningHook.choices)
    caption_style = _valid_choice(request.POST.get("caption_style"), ContentPerformanceProfile.CaptionStyle.choices)
    season = _valid_choice(request.POST.get("season"), ContentPerformanceProfile.Season.choices)
    subject = _valid_choice(request.POST.get("subject"), ContentPerformanceProfile.Subject.choices)
    if None in {source_type, opening_hook, caption_style, season, subject}:
        messages.error(request, "Choose valid learning labels for this post.")
        return redirect(return_to)
    target_key = str(request.POST.get("target_key") or "").strip()
    target = None
    if target_key:
        if "::" not in target_key:
            messages.error(request, "Choose a TN Game target from the existing target catalog.")
            return redirect(return_to)
        target_type, target_id = target_key.split("::", 1)
        target = find_catalog_target(workspace, target_type, target_id)
        if target is None:
            messages.error(request, "Choose a TN Game target from the existing target catalog.")
            return redirect(return_to)
    inferred_submission = inferred_ugc_for_post(workspace, platform_post.post_id)
    defaults = {
        "source_submission": inferred_submission,
        "creator": inferred_submission.creator if inferred_submission else None,
        "target_type": inferred_submission.target_type if inferred_submission else "",
        "target_id": inferred_submission.target_id if inferred_submission else "",
        "target_label": inferred_submission.target_label if inferred_submission else "",
        "target_url": inferred_submission.target_url if inferred_submission else "",
        "created_by": request.user,
    }
    profile, _created = ContentPerformanceProfile.objects.get_or_create(
        workspace=workspace,
        post=platform_post.post,
        defaults=defaults,
    )
    before = {
        "source_type": profile.source_type,
        "opening_hook": profile.opening_hook,
        "caption_style": profile.caption_style,
        "season": profile.season,
        "subject": profile.subject,
        "target_type": profile.target_type,
        "target_id": profile.target_id,
    }
    profile.source_type = source_type
    profile.opening_hook = opening_hook
    profile.caption_style = caption_style
    profile.season = season
    profile.subject = subject
    profile.notes = str(request.POST.get("notes") or "").strip()[:5000]
    if target:
        profile.target_type = target["target_type"]
        profile.target_id = target["target_id"]
        profile.target_label = target["target_label"]
        profile.target_url = target.get("target_url") or ""
    else:
        profile.target_type = ""
        profile.target_id = ""
        profile.target_label = ""
        profile.target_url = ""
    if inferred_submission and profile.source_submission_id is None:
        profile.source_submission = inferred_submission
        profile.creator = inferred_submission.creator
    profile.updated_by = request.user
    profile.save()
    after = {
        "source_type": profile.source_type,
        "opening_hook": profile.opening_hook,
        "caption_style": profile.caption_style,
        "season": profile.season,
        "subject": profile.subject,
        "target_type": profile.target_type,
        "target_id": profile.target_id,
    }
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.performance_profile_updated",
        target=profile,
        metadata={
            "post_id": str(platform_post.post_id),
            "platform_post_id": str(platform_post.id),
            "account_id": str(platform_post.social_account_id),
            "before": before,
            "after": after,
        },
        request=request,
    )
    messages.success(request, "Learning labels saved. Studio will include this post in future lessons.")
    return redirect(return_to)
