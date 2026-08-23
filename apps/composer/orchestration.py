"""Server-side coordination helpers for multi-account Studio posts.

The orchestration surface deliberately reuses ``Post`` and ``PlatformPost``.
It adds no second campaign or publishing state: a shared Post is the content
idea and each PlatformPost remains the independently editable account variant.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Q
from django.utils import timezone

from apps.common.models import UGCSubmission
from apps.common.ugc_creator_services import rights_can_use
from apps.social_accounts.models import SocialAccount

from .models import PlatformPost, Post

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


def _legacy_ugc_map(workspace, post_ids):
    """Map older UGC drafts that predate ContentPerformanceProfile provenance."""
    wanted = {str(value) for value in post_ids}
    result = {}
    submissions = UGCSubmission.objects.for_workspace(workspace.id).select_related("rights_passport")
    for submission in submissions.iterator(chunk_size=250):
        for post_id in (submission.metadata or {}).get("studio_post_ids") or []:
            key = str(post_id)
            if key in wanted:
                result[key] = submission
    return result


def submission_for_post(post, legacy_map=None):
    try:
        profile = post.performance_profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if profile is not None and profile.source_submission_id:
        return profile.source_submission
    return (legacy_map or {}).get(str(post.id))


def find_submission_for_post(workspace, post):
    """Resolve current and legacy UGC provenance for a single mutation."""
    submission = submission_for_post(post)
    if submission is not None:
        return submission
    return submission_for_post(post, _legacy_ugc_map(workspace, [post.id]))


def account_rights(submission, account):
    """Return whether one UGC asset may be used on one connected account."""
    if submission is None:
        return True, ""
    if submission.status != UGCSubmission.Status.APPROVED:
        return False, "Community content is no longer approved."
    if not submission.consent_confirmed:
        return False, "Contributor consent is required."
    allowed, error = rights_can_use(submission, "organic_social")
    if not allowed:
        return False, error
    passport = submission.rights_passport
    allowed_ids = {str(value) for value in (passport.allowed_account_ids or [])}
    if allowed_ids and str(account.id) not in allowed_ids:
        return False, "The rights passport does not allow this social account."
    return True, ""


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
        action_needed = any(item.status in ACTION_STATUSES for item in variants)
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
            }
        )

    account_summaries = []
    for account in accounts:
        account_variants = [
            variant for row in rows for variant in row["variants"] if variant.social_account_id == account.id
        ]
        future = [
            _effective_schedule(item)
            for item in account_variants
            if item.status == PlatformPost.Status.SCHEDULED and _effective_schedule(item) is not None
        ]
        account_summaries.append(
            {
                "account": account,
                "scheduled_count": len(future),
                "attention_count": sum(item.status in ACTION_STATUSES for item in account_variants),
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
