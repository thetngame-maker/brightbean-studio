"""PostgreSQL-safe Smart Plan commit path.

The preview query joins nullable creator/media/rights rows. PostgreSQL rejects
FOR UPDATE against nullable outer-join relations. The commit path therefore
locks only the base submission/account rows and reads related objects normally.
"""

import zoneinfo
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from apps.composer.models import PlatformPost
from apps.composer.services import create_post
from apps.composer.ugc_publish_guard import account_rights
from apps.social_accounts.models import SocialAccount

from .audit import record_audit_event
from .models import ContentPerformanceProfile, UGCSubmission
from .ugc_provenance import get_provenance
from .ugc_smart_planning import (
    DEFAULT_DAILY_LIMIT,
    DIRECT_SCHEDULE_BLOCKED_MODES,
    MAX_DAILY_LIMIT,
    MAX_PLAN_COUNT,
    MIN_DAILY_LIMIT,
    PLAN_LOOKAHEAD_DAYS,
    SmartPlanError,
    _caption_for_account,
    _media_works_for_account,
    _parse_slot,
    _quality_for,
)


def _bounded_capacity(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(MAX_DAILY_LIMIT, parsed))


def commit_smart_plan(workspace, payload, *, actor, caption_overrides=None):
    if str(payload.get("workspace_id")) != str(workspace.id):
        raise SmartPlanError("This plan belongs to another workspace.")
    raw_items = list(payload.get("items") or [])
    if not raw_items or len(raw_items) > MAX_PLAN_COUNT:
        raise SmartPlanError("Refresh the Smart Plan before scheduling.")

    direct_schedule = workspace.approval_workflow_mode not in DIRECT_SCHEDULE_BLOCKED_MODES
    now = timezone.now()
    created = []
    constraints = payload.get("constraints") or {}
    enforce_capacity = bool(constraints)
    daily_limit = max(
        MIN_DAILY_LIMIT,
        _bounded_capacity(constraints.get("daily_limit"), DEFAULT_DAILY_LIMIT),
    )
    date_limits = {
        str(day): _bounded_capacity(limit, daily_limit) for day, limit in (constraints.get("date_limits") or {}).items()
    }
    workspace_tz = zoneinfo.ZoneInfo(workspace.effective_timezone or "UTC")

    with transaction.atomic():
        # PostgreSQL cannot apply FOR UPDATE to nullable relations introduced by
        # select_related(). Lock only each base row; related rights/media/creator
        # objects are read-only during this transaction.
        submissions = {
            str(item.id): item
            for item in UGCSubmission.objects.select_for_update(of=("self",))
            .filter(workspace=workspace, id__in=[row.get("submission_id") for row in raw_items])
            .select_related("creator", "media_asset", "rights_passport")
        }
        accounts = {
            str(item.id): item
            for item in SocialAccount.objects.select_for_update(of=("self",)).filter(
                workspace=workspace,
                connection_status=SocialAccount.ConnectionStatus.CONNECTED,
                id__in=[row.get("account_id") for row in raw_items],
            )
        }
        existing_daily_counts = defaultdict(int)
        planned_daily_counts = defaultdict(int)
        if enforce_capacity and accounts:
            slot_dates = [_parse_slot(row.get("scheduled_at")).astimezone(workspace_tz).date() for row in raw_items]
            window_start = datetime.combine(min(slot_dates), time.min, tzinfo=workspace_tz)
            window_end = datetime.combine(max(slot_dates) + timedelta(days=1), time.min, tzinfo=workspace_tz)
            for scheduled_at in PlatformPost.objects.filter(
                social_account_id__in=accounts,
                scheduled_at__gte=window_start,
                scheduled_at__lt=window_end,
            ).values_list("scheduled_at", flat=True):
                existing_daily_counts[scheduled_at.astimezone(workspace_tz).date()] += 1
            for proposed_at in PlatformPost.objects.filter(
                social_account_id__in=accounts,
                scheduled_at__isnull=True,
                post__proposed_publish_at__gte=window_start,
                post__proposed_publish_at__lt=window_end,
            ).values_list("post__proposed_publish_at", flat=True):
                existing_daily_counts[proposed_at.astimezone(workspace_tz).date()] += 1

        for row in raw_items:
            submission = submissions.get(str(row.get("submission_id")))
            account = accounts.get(str(row.get("account_id")))
            slot_at = _parse_slot(row.get("scheduled_at"))

            if submission is None or account is None:
                raise SmartPlanError("A planned content item or account is no longer available.")
            if slot_at <= now + timedelta(minutes=15) or slot_at > now + timedelta(days=PLAN_LOOKAHEAD_DAYS + 1):
                raise SmartPlanError("A planned time is no longer available. Refresh the plan.")
            if submission.status != UGCSubmission.Status.APPROVED or (submission.metadata or {}).get("studio_post_ids"):
                raise SmartPlanError("One of these community items was already used or is no longer approved.")
            if not submission.consent_confirmed or _quality_for(submission)["needs_check"]:
                raise SmartPlanError("One of these community items now needs a rights or quality check.")

            allowed, error = account_rights(submission, account)
            if not allowed:
                raise SmartPlanError(error)
            if not _media_works_for_account(submission, account):
                raise SmartPlanError(f"{account.display_label} requires media for this planned post.")
            local_date = slot_at.astimezone(workspace_tz).date()
            capacity = date_limits.get(local_date.isoformat(), daily_limit)
            if enforce_capacity and existing_daily_counts[local_date] + planned_daily_counts[local_date] >= capacity:
                raise SmartPlanError(
                    "A selected date just reached its posting limit. Refresh the plan to use the next opening."
                )
            if (
                direct_schedule
                and PlatformPost.objects.filter(
                    social_account=account,
                    scheduled_at__gte=slot_at - timedelta(minutes=29),
                    scheduled_at__lte=slot_at + timedelta(minutes=29),
                ).exists()
            ):
                raise SmartPlanError("A planned time was just filled. Refresh the plan to use the next opening.")

            override = (caption_overrides or {}).get(str(submission.id))
            caption = _caption_for_account(submission, account, lead_override=override)
            provenance = get_provenance(submission.metadata)
            passport = submission.rights_passport
            reason = str(row.get("reason") or "Smart Plan recommendation")[:500]
            notes = [
                "Scheduled by Approved Smart Plan" if direct_schedule else "Timed draft created by Approved Smart Plan",
                f"UGC submission: {submission.id}",
                f"Target: {submission.target_type}:{submission.target_id}",
                f"Target name: {submission.target_label}",
                f"Social account: {account.display_label}",
                f"Planning evidence: {reason}",
                f"Rights passport: {passport.id}",
                f"Rights scopes: {', '.join(passport.scope_labels) or 'None'}",
            ]
            if provenance.get("source_url"):
                notes.append(f"Original source URL: {provenance['source_url']}")
            if passport.credit_required and passport.credit_text:
                notes.append(f"Required credit: {passport.credit_text}")

            post = create_post(
                workspace=workspace,
                social_account=account,
                caption=caption,
                media_asset_ids=[submission.media_asset_id] if submission.media_asset_id else [],
                title=submission.title or submission.target_label or "Community content",
                internal_notes="\n".join(notes),
                scheduled_at=slot_at if direct_schedule else None,
                proposed_publish_at=None if direct_schedule else slot_at,
                author=actor,
                status="scheduled" if direct_schedule else "draft",
            )
            planned_daily_counts[local_date] += 1
            ContentPerformanceProfile.objects.create(
                workspace=workspace,
                post=post,
                source_submission=submission,
                creator=submission.creator,
                source_type=ContentPerformanceProfile.SourceType.UGC,
                target_type=submission.target_type,
                target_id=submission.target_id,
                target_label=submission.target_label,
                target_url=submission.target_url,
                notes=f"Smart Plan evidence: {reason}",
                created_by=actor,
                updated_by=actor,
            )

            metadata = dict(submission.metadata or {})
            post_ids = [str(value) for value in metadata.get("studio_post_ids") or [] if value]
            post_ids.append(str(post.id))
            metadata["studio_post_ids"] = post_ids[-20:]
            metadata["studio_drafted_at"] = now.isoformat()
            metadata["smart_plan"] = {
                "post_id": str(post.id),
                "social_account_id": str(account.id),
                "planned_for": slot_at.isoformat(),
                "committed_at": now.isoformat(),
                "mode": "scheduled" if direct_schedule else "approval_draft",
            }
            submission.metadata = metadata
            submission.save(update_fields=["metadata", "updated_at"])
            record_audit_event(
                workspace=workspace,
                actor=actor,
                action="ugc.smart_plan_scheduled" if direct_schedule else "ugc.smart_plan_draft_created",
                target=submission,
                metadata={
                    "post_id": str(post.id),
                    "social_account_id": str(account.id),
                    "planned_for": slot_at.isoformat(),
                    "rights_passport_id": str(passport.id),
                    "reason": reason,
                },
            )
            created.append(post)

    return created, direct_schedule
