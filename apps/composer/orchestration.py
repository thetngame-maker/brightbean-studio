"""Server-side coordination helpers for multi-account Studio posts.

The orchestration surface deliberately reuses ``Post`` and ``PlatformPost``.
It adds no second campaign or publishing state: a shared Post is the content
idea and each PlatformPost remains the independently editable account variant.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from apps.social_accounts.models import SocialAccount

from .models import PlatformPost, Post
from .ugc_publish_guard import _legacy_ugc_map, account_rights, post_publish_preflight, submission_for_post

ACTION_STATUSES = {
    PlatformPost.Status.FAILED,
    PlatformPost.Status.CHANGES_REQUESTED,
    PlatformPost.Status.REJECTED,
    PlatformPost.Status.ON_HOLD,
}


def connected_accounts(workspace):
    return list(
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .order_by("account_name", "account_handle", "platform")
    )


def _effective_schedule(platform_post):
    return platform_post.scheduled_at or platform_post.post.scheduled_at


def tight_rollout_count(workspace, *, limit=500):
    """Count scheduled Posts whose account launches are under 30 minutes apart."""
    variants = list(
        PlatformPost.objects.filter(
            post__workspace=workspace,
            status=PlatformPost.Status.SCHEDULED,
        )
        .select_related("post")
        .order_by("post_id", "scheduled_at")[:limit]
    )
    by_post = {}
    for variant in variants:
        effective_at = _effective_schedule(variant)
        if effective_at is not None:
            by_post.setdefault(variant.post_id, []).append(effective_at)
    count = 0
    for times in by_post.values():
        times.sort()
        if any(right - left < timedelta(minutes=30) for left, right in zip(times, times[1:], strict=False)):
            count += 1
    return count


def build_orchestration(workspace, *, limit=300):
    """Build recent coordination rows and connected-account workload totals."""
    accounts = connected_accounts(workspace)
    posts = list(
        Post.objects.for_workspace(workspace.id)
        .filter(platform_posts__isnull=False)
        .filter(
            Q(updated_at__gte=timezone.now() - timedelta(days=180))
            | ~Q(platform_posts__status=PlatformPost.Status.PUBLISHED)
        )
        .select_related("performance_profile__source_submission__rights_passport")
        .prefetch_related("platform_posts__social_account")
        .distinct()
        .order_by("-updated_at")[:limit]
    )
    legacy_map = _legacy_ugc_map(workspace, [post.id for post in posts])
    account_by_id = {account.id: account for account in accounts}
    rows = []
    for post in posts:
        variants = sorted(
            list(post.platform_posts.all()),
            key=lambda item: (
                _effective_schedule(item) or timezone.now() + timedelta(days=36500),
                item.social_account.display_label.lower(),
            ),
        )
        variant_account_ids = {item.social_account_id for item in variants}
        submission = submission_for_post(post, legacy_map)
        publish_guard = post_publish_preflight(
            workspace,
            post,
            variants,
            submission_override=submission,
        )
        eligible_missing = []
        blocked_missing = []
        for account_id, account in account_by_id.items():
            if account_id in variant_account_ids:
                continue
            allowed, error = account_rights(submission, account)
            (eligible_missing if allowed else blocked_missing).append({"account": account, "reason": error})
        scheduled = [
            item
            for item in variants
            if item.status == PlatformPost.Status.SCHEDULED and _effective_schedule(item) is not None
        ]
        scheduled.sort(key=_effective_schedule)
        gaps = [
            _effective_schedule(right) - _effective_schedule(left)
            for left, right in zip(scheduled, scheduled[1:], strict=False)
        ]
        tight_rollout = len(scheduled) > 1 and any(gap < timedelta(minutes=30) for gap in gaps)
        action_needed = any(item.status in ACTION_STATUSES for item in variants) or bool(publish_guard["blockers"])
        rows.append(
            {
                "post": post,
                "variants": variants,
                "variant_count": len(variants),
                "coverage_percent": round((len(variants) / len(accounts)) * 100) if accounts else 0,
                "eligible_missing": eligible_missing,
                "blocked_missing": blocked_missing,
                "is_coordinated": len(variants) > 1,
                "action_needed": action_needed,
                "scheduled_count": len(scheduled),
                "tight_rollout": tight_rollout,
                "is_ugc": submission is not None,
                "ugc_submission": submission,
                "publish_guard": publish_guard,
            }
        )

    account_summaries = []
    for account in accounts:
        account_variants = [
            variant for row in rows for variant in row["variants"] if variant.social_account_id == account.id
        ]
        guarded_variant_ids = {
            blocker["platform_post_id"]
            for row in rows
            for blocker in row["publish_guard"]["blockers"]
            if blocker["social_account_id"] == str(account.id)
        }
        future = [
            _effective_schedule(item)
            for item in account_variants
            if item.status == PlatformPost.Status.SCHEDULED and _effective_schedule(item) is not None
        ]
        account_summaries.append(
            {
                "account": account,
                "scheduled_count": len(future),
                "attention_count": sum(
                    item.status in ACTION_STATUSES or str(item.id) in guarded_variant_ids
                    for item in account_variants
                ),
                "next_at": min(future) if future else None,
            }
        )
    return {
        "rows": rows,
        "accounts": account_summaries,
        "counts": {
            "all": len(rows),
            "coordinated": sum(row["is_coordinated"] for row in rows),
            "action": sum(row["action_needed"] for row in rows),
            "spacing": sum(row["tight_rollout"] for row in rows),
        },
        "limit_reached": len(posts) == limit,
    }
