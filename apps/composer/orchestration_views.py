"""Lightweight, server-rendered multi-account orchestration views."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.common.audit import record_audit_event
from apps.common.ugc_creator_views import _safe_local_path
from apps.common.ugc_views import _get_workspace
from apps.members.decorators import require_permission
from apps.social_accounts.models import SocialAccount

from .models import PlatformPost, Post
from .orchestration import build_orchestration
from .services import sync_post_scheduled_at
from .ugc_publish_guard import account_rights, find_submission_for_post

ORCHESTRATION_PAGE_SIZE = 12
VALID_QUEUES = {"all", "coordinated", "action", "spacing"}
SPACING_CHOICES = {30, 60, 120, 1440}


def _return_path(request, workspace):
    fallback = reverse("composer:orchestration", kwargs={"workspace_id": workspace.id})
    return _safe_local_path(request, request.POST.get("return_to"), fallback)


@login_required
@require_permission("create_posts")
def orchestration(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    data = build_orchestration(workspace)
    queue = str(request.GET.get("view") or "all").strip().lower()
    if queue not in VALID_QUEUES:
        queue = "all"
    query = str(request.GET.get("q") or "").strip()[:120]
    rows = data["rows"]
    if queue == "coordinated":
        rows = [row for row in rows if row["is_coordinated"]]
    elif queue == "action":
        rows = [row for row in rows if row["action_needed"]]
    elif queue == "spacing":
        rows = [row for row in rows if row["tight_rollout"]]
    if query:
        lowered = query.lower()
        rows = [
            row
            for row in rows
            if lowered in (row["post"].title or "").lower()
            or lowered in (row["post"].caption or "").lower()
            or any(lowered in variant.social_account.display_label.lower() for variant in row["variants"])
        ]
    page = Paginator(rows, ORCHESTRATION_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    return render(
        request,
        "composer/orchestration.html",
        {
            "workspace": workspace,
            "orchestration_rows": page.object_list,
            "orchestration_page": page,
            "orchestration_accounts": data["accounts"],
            "orchestration_counts": data["counts"],
            "orchestration_limit_reached": data["limit_reached"],
            "orchestration_queue": queue,
            "orchestration_query": query,
        },
    )


@login_required
@require_permission("create_posts")
@require_POST
def add_orchestration_variant(request, workspace_id, post_id):
    workspace = _get_workspace(request, workspace_id)
    return_to = _return_path(request, workspace)
    post = get_object_or_404(
        Post.objects.select_related("performance_profile__source_submission__rights_passport"),
        id=post_id,
        workspace=workspace,
    )
    account = get_object_or_404(
        SocialAccount,
        id=request.POST.get("account_id"),
        workspace=workspace,
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )
    submission = find_submission_for_post(workspace, post)
    allowed, error = account_rights(submission, account)
    if not allowed:
        messages.error(request, error)
        return redirect(return_to)
    platform_post, created = PlatformPost.objects.get_or_create(
        post=post,
        social_account=account,
        defaults={"status": PlatformPost.Status.DRAFT},
    )
    if not created:
        messages.info(request, f"{account.display_label} is already part of this content idea.")
        return redirect(return_to)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="composer.orchestration_variant_added",
        target=platform_post,
        metadata={
            "post_id": str(post.id),
            "social_account_id": str(account.id),
            "status": platform_post.status,
            "ugc_submission_id": str(submission.id) if submission else "",
        },
        request=request,
    )
    messages.success(request, f"Draft variant added for {account.display_label}. Tailor it before scheduling.")
    return redirect(return_to)


@login_required
@require_permission("create_posts")
@require_POST
def stagger_orchestration(request, workspace_id, post_id):
    workspace = _get_workspace(request, workspace_id)
    return_to = _return_path(request, workspace)
    post = get_object_or_404(Post, id=post_id, workspace=workspace)
    try:
        spacing_minutes = int(request.POST.get("spacing_minutes") or 60)
    except (TypeError, ValueError):
        spacing_minutes = 60
    if spacing_minutes not in SPACING_CHOICES:
        messages.error(request, "Choose a valid rollout spacing.")
        return redirect(return_to)
    variants = list(
        post.platform_posts.select_related("social_account")
        .filter(status=PlatformPost.Status.SCHEDULED)
        .order_by("scheduled_at", "social_account__account_name", "created_at")
    )
    times = [variant.scheduled_at or post.scheduled_at for variant in variants]
    if len(variants) < 2 or not all(times):
        messages.error(request, "At least two scheduled account variants are required to stagger a rollout.")
        return redirect(return_to)
    anchor = min(value for value in times if value is not None)
    from datetime import timedelta

    before = {str(variant.id): (variant.scheduled_at or post.scheduled_at).isoformat() for variant in variants}
    after = {}
    with transaction.atomic():
        for index, variant in enumerate(variants):
            variant.scheduled_at = anchor + timedelta(minutes=spacing_minutes * index)
            variant.save(update_fields=["scheduled_at", "updated_at"])
            after[str(variant.id)] = variant.scheduled_at.isoformat()
        sync_post_scheduled_at(post)
        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action="composer.orchestration_rollout_staggered",
            target=post,
            metadata={
                "spacing_minutes": spacing_minutes,
                "before": before,
                "after": after,
            },
            request=request,
        )
    messages.success(request, f"Rollout staggered by {spacing_minutes} minutes across {len(variants)} accounts.")
    return redirect(return_to)
