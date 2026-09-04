"""Evidence-backed one-tap planning for approved community content."""

from __future__ import annotations

import math
import re
import statistics
import zoneinfo
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.composer.models import PlatformPost
from apps.composer.services import create_post
from apps.composer.ugc_publish_guard import account_rights
from apps.social_accounts.models import SocialAccount

from .audit import record_audit_event
from .models import ContentPerformanceProfile, UGCSubmission
from .ugc_creator_services import rights_can_use
from .ugc_mobile_quality import approved_quality
from .ugc_performance_learning import build_performance_learning
from .ugc_provenance import get_provenance
from .ugc_relevance import score_relevance

DEFAULT_PLAN_COUNT = 7
DEFAULT_PLAN_DAYS = 14
DEFAULT_DAILY_LIMIT = 2
MIN_PLAN_COUNT = 1
MAX_PLAN_COUNT = 30
MIN_PLAN_DAYS = 1
MAX_PLAN_DAYS = 60
MIN_DAILY_LIMIT = 1
MAX_DAILY_LIMIT = 10
PLAN_LOOKAHEAD_DAYS = 60
DIRECT_SCHEDULE_BLOCKED_MODES = {"required_internal", "required_internal_and_client"}
GENERIC_ACCOUNT_WORDS = {
    "the",
    "tn",
    "tennessee",
    "game",
    "official",
    "instagram",
    "facebook",
    "page",
}


class SmartPlanError(ValueError):
    pass


def _normalized_weekdays(values):
    selected = set()
    for value in range(7) if values is None else values:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            selected.add(day)
    return sorted(selected)


def _number(value):
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(str(value or 0).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _candidate_metrics(submission):
    discovery = (submission.metadata or {}).get("discovery_import") or {}
    likes = _number(discovery.get("like_count"))
    comments = _number(discovery.get("comment_count"))
    views = _number(discovery.get("view_count"))
    raw_score = likes + (comments * 3) + (views * 0.02)
    return {
        "likes": likes,
        "comments": comments,
        "views": views,
        "raw_score": raw_score,
        "score": math.log1p(raw_score) * 18,
    }


def _quality_for(submission):
    metadata = submission.metadata or {}
    provenance = get_provenance(metadata)
    discovery = metadata.get("discovery_import") or {}
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
    return approved_quality(submission)


def _ready_candidates(workspace):
    rows = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(status=UGCSubmission.Status.APPROVED)
        .select_related("creator", "media_asset", "rights_passport")
        .order_by("-submitted_at")[:500]
    )
    candidates = []
    now = timezone.now()
    for submission in rows:
        metadata = submission.metadata or {}
        if metadata.get("studio_post_ids") or not submission.consent_confirmed:
            continue
        allowed, _error = rights_can_use(submission, "organic_social")
        if not allowed or _quality_for(submission)["needs_check"]:
            continue
        metrics = _candidate_metrics(submission)
        age_days = max(0, (now - submission.submitted_at).total_seconds() / 86400)
        freshness = max(0, 18 - min(18, age_days * 0.15))
        candidates.append(
            {
                "submission": submission,
                "metrics": metrics,
                "base_score": metrics["score"] + freshness,
                "format_key": "video"
                if submission.media_asset and submission.media_asset.is_video
                else "photo"
                if submission.media_asset and submission.media_asset.is_image
                else "text",
            }
        )
    candidates.sort(key=lambda item: (-item["base_score"], -item["submission"].submitted_at.timestamp()))
    for index, candidate in enumerate(candidates, start=1):
        candidate["engagement_rank"] = index
    return candidates


def _connected_accounts(workspace, account_ids=None):
    queryset = (
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .prefetch_related("posting_slots")
        .order_by("account_name", "account_handle", "platform")
    )
    selected = {str(value) for value in (account_ids or []) if value}
    if selected:
        queryset = queryset.filter(id__in=selected)
    return list(queryset)


def _median_cadence_days(history):
    published = sorted(
        {row["platform_post"].published_at for row in history if row["platform_post"].published_at},
        reverse=True,
    )[:20]
    gaps = [
        (left - right).total_seconds() / 86400
        for left, right in zip(published, published[1:], strict=False)
        if left > right
    ]
    if not gaps:
        return 2
    return max(1, min(4, round(statistics.median(gaps))))


def _window_label(day, slot_time):
    day_name = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[day]
    value = datetime.combine(timezone.now().date(), slot_time).strftime("%-I:%M %p")
    return f"{day_name} · {value}"


def _account_insight(account, history, workspace_tz):
    history = list(history)
    observed = defaultdict(list)
    recent_targets = {}
    recent_creators = {}
    format_scores = defaultdict(list)
    target_scores = defaultdict(list)
    for row in history:
        published_at = row["platform_post"].published_at
        if published_at:
            local = published_at.astimezone(workspace_tz)
            rounded_minute = 15 * round(local.minute / 15)
            hour = local.hour + (1 if rounded_minute == 60 else 0)
            slot_time = time(hour % 24, 0 if rounded_minute == 60 else rounded_minute)
            observed[(local.weekday(), slot_time)].append(float(row.get("relative_index") or 100))
            if row.get("target_label"):
                key = row["target_label"].strip().casefold()
                recent_targets.setdefault(key, published_at)
            creator = row.get("creator")
            if creator:
                recent_creators.setdefault(str(creator.id), published_at)
        score = float(row.get("relative_index") or 100)
        if row.get("format_key"):
            format_scores[row["format_key"]].append(score)
        if row.get("target_label"):
            target_scores[row["target_label"].strip().casefold()].append(score)

    explicit_slots = [slot for slot in account.posting_slots.all() if slot.is_active]
    patterns = []
    if explicit_slots:
        for slot in explicit_slots:
            scores = [
                score
                for (day, observed_time), values in observed.items()
                if day == slot.day_of_week
                and abs((observed_time.hour * 60 + observed_time.minute) - (slot.time.hour * 60 + slot.time.minute))
                <= 120
                for score in values
            ]
            patterns.append(
                {
                    "day": slot.day_of_week,
                    "time": slot.time,
                    "score": sum(scores) / len(scores) if scores else 100,
                    "sample_count": len(scores),
                }
            )
    elif observed:
        for (day, slot_time), scores in observed.items():
            patterns.append(
                {
                    "day": day,
                    "time": slot_time,
                    "score": sum(scores) / len(scores),
                    "sample_count": len(scores),
                }
            )
    else:
        for day in (1, 3, 5):
            patterns.append({"day": day, "time": time(10, 0), "score": 100, "sample_count": 0})
    patterns.sort(key=lambda item: (-item["score"], -item["sample_count"], item["day"], item["time"]))
    best = patterns[0]
    return {
        "account": account,
        "history": history,
        "recent_count": len(history),
        "cadence_days": _median_cadence_days(history),
        "patterns": patterns,
        "best_window": _window_label(best["day"], best["time"]),
        "best_window_sample": best["sample_count"],
        "recent_targets": recent_targets,
        "recent_creators": recent_creators,
        "format_scores": {key: sum(values) / len(values) for key, values in format_scores.items()},
        "target_scores": {key: sum(values) / len(values) for key, values in target_scores.items()},
    }


def _available_times(
    account,
    insight,
    workspace_tz,
    *,
    count,
    days,
    start_date=None,
    end_date=None,
    weekdays=None,
    daily_limit=DEFAULT_DAILY_LIMIT,
    date_limits=None,
):
    now = timezone.now()
    floor = now + timedelta(hours=2)
    existing = list(
        PlatformPost.objects.filter(
            social_account=account,
            scheduled_at__gt=now,
        )
        .exclude(status__in=PlatformPost.PROTECTED_STATUSES)
        .values_list("scheduled_at", flat=True)
    )
    existing.extend(
        PlatformPost.objects.filter(
            social_account=account,
            scheduled_at__isnull=True,
            post__proposed_publish_at__gt=now,
        )
        .exclude(status__in=PlatformPost.PROTECTED_STATUSES)
        .values_list("post__proposed_publish_at", flat=True)
    )
    candidates = []
    local_floor = floor.astimezone(workspace_tz)
    planning_start = max(start_date or local_floor.date(), local_floor.date())
    planning_end = end_date or (planning_start + timedelta(days=min(days, PLAN_LOOKAHEAD_DAYS) - 1))
    planning_end = min(planning_end, planning_start + timedelta(days=PLAN_LOOKAHEAD_DAYS - 1))
    selected_weekdays = set(range(7)) if weekdays is None else set(weekdays)
    limits_by_date = date_limits or {}
    for offset in range((planning_end - planning_start).days + 1):
        date = planning_start + timedelta(days=offset)
        if date.weekday() not in selected_weekdays:
            continue
        effective_limit = max(0, min(MAX_DAILY_LIMIT, int(limits_by_date.get(date, daily_limit))))
        if effective_limit == 0:
            continue
        for pattern in insight["patterns"]:
            if date.weekday() != pattern["day"]:
                continue
            slot_at = datetime.combine(date, pattern["time"], tzinfo=workspace_tz)
            if slot_at <= floor:
                continue
            if any(abs((slot_at - occupied).total_seconds()) < 1800 for occupied in existing):
                continue
            preference = offset - max(-2, min(2, (pattern["score"] - 100) / 25))
            candidates.append((preference, slot_at, pattern))
    candidates.sort(key=lambda item: (item[0], item[1]))
    min_gap = timedelta(hours=max(18, (insight["cadence_days"] * 24) - 6))
    chosen = []
    anchors = list(existing)
    for _preference, slot_at, pattern in candidates:
        if any(abs(slot_at - anchor) < min_gap for anchor in anchors):
            continue
        chosen.append((slot_at, pattern))
        anchors.append(slot_at)
        if len(chosen) >= count:
            break
    return chosen


def _words(value):
    words = {word for word in re.findall(r"[a-z0-9]+", str(value or "").casefold()) if len(word) > 2}
    if any(word.startswith("fall") or word == "waterfall" for word in words):
        words.add("fall")
    return words


def _account_fit(candidate, insight, planned_targets, planned_creators):
    submission = candidate["submission"]
    score = 0.0
    reasons = []
    target_key = (submission.target_label or "").strip().casefold()
    creator_key = str(submission.creator_id or "")
    account_words = (
        _words(f"{insight['account'].display_label} {insight['account'].account_handle}") - GENERIC_ACCOUNT_WORDS
    )
    content_words = _words(f"{submission.target_label} {submission.title} {submission.body}")
    if "waterfalls" in account_words and any(word.startswith("fall") or word == "waterfall" for word in content_words):
        account_words.add("fall")
    overlap = account_words & content_words
    if overlap:
        score += min(36, len(overlap) * 18)
        reasons.append(f"fits {insight['account'].display_label}")
    target_lift = insight["target_scores"].get(target_key)
    if target_lift:
        score += max(-15, min(25, (target_lift - 100) / 2))
        if target_lift >= 110:
            reasons.append("this target has performed well here")
    format_lift = insight["format_scores"].get(candidate["format_key"])
    if format_lift:
        score += max(-10, min(18, (format_lift - 100) / 3))
        if format_lift >= 112:
            reasons.append(f"{candidate['format_key']} posts perform well")
    recent_target_at = insight["recent_targets"].get(target_key)
    if recent_target_at:
        days = (timezone.now() - recent_target_at).total_seconds() / 86400
        if days < 14:
            score -= 42 - (days * 3)
    recent_creator_at = insight["recent_creators"].get(creator_key)
    if creator_key and recent_creator_at:
        days = (timezone.now() - recent_creator_at).total_seconds() / 86400
        if days < 10:
            score -= 30 - (days * 3)
    if target_key and target_key in planned_targets[-3:]:
        score -= 48
    if creator_key and creator_key in planned_creators[-2:]:
        score -= 35
    return score, reasons


def _media_works_for_account(submission, account):
    if account.platform in {"instagram", "instagram_login", "tiktok", "pinterest"}:
        return submission.media_asset_id is not None
    return True


def _engagement_label(metrics):
    bits = []
    if metrics["likes"]:
        bits.append(f"{metrics['likes']:,} likes")
    if metrics["comments"]:
        bits.append(f"{metrics['comments']:,} comments")
    if metrics["views"]:
        bits.append(f"{metrics['views']:,} views")
    return " · ".join(bits) or "No imported engagement yet"


def build_smart_plan(
    workspace,
    *,
    count=DEFAULT_PLAN_COUNT,
    days=DEFAULT_PLAN_DAYS,
    account_ids=None,
    start_date=None,
    end_date=None,
    weekdays=None,
    daily_limit=DEFAULT_DAILY_LIMIT,
    date_limits=None,
):
    count = max(MIN_PLAN_COUNT, min(MAX_PLAN_COUNT, int(count)))
    days = max(MIN_PLAN_DAYS, min(MAX_PLAN_DAYS, int(days)))
    daily_limit = max(MIN_DAILY_LIMIT, min(MAX_DAILY_LIMIT, int(daily_limit)))
    workspace_tz = zoneinfo.ZoneInfo(workspace.effective_timezone or "UTC")
    local_today = timezone.now().astimezone(workspace_tz).date()
    last_available = local_today + timedelta(days=PLAN_LOOKAHEAD_DAYS - 1)
    planning_start = min(max(start_date or local_today, local_today), last_available)
    planning_end = end_date or (planning_start + timedelta(days=days - 1))
    planning_end = max(planning_start, min(planning_end, last_available))
    days = (planning_end - planning_start).days + 1
    selected_weekdays = _normalized_weekdays(weekdays)
    limits_by_date = {
        day: max(0, min(MAX_DAILY_LIMIT, int(limit)))
        for day, limit in (date_limits or {}).items()
        if planning_start <= day <= planning_end
    }
    accounts = _connected_accounts(workspace, account_ids)
    if not accounts:
        return {
            "items": [],
            "accounts": [],
            "candidate_count": 0,
            "requested_count": count,
            "requested_days": days,
            "start_date": planning_start,
            "end_date": planning_end,
            "daily_limit": daily_limit,
            "weekdays": selected_weekdays,
            "date_limits": limits_by_date,
            "daily_counts": {},
            "existing_daily_counts": {},
            "direct_schedule": False,
            "empty_reason": "Connect or select at least one social account before planning.",
        }
    candidates = _ready_candidates(workspace)
    learning = build_performance_learning(workspace, days=90, limit=300)
    history_by_account = defaultdict(list)
    for row in learning["rows"]:
        history_by_account[row["account_id"]].append(row)
    insights = [_account_insight(account, history_by_account.get(account.id, []), workspace_tz) for account in accounts]
    slots_by_account = {
        insight["account"].id: _available_times(
            insight["account"],
            insight,
            workspace_tz,
            count=count,
            days=days,
            start_date=planning_start,
            end_date=planning_end,
            weekdays=selected_weekdays,
            daily_limit=daily_limit,
            date_limits=limits_by_date,
        )
        for insight in insights
    }
    window_start = datetime.combine(planning_start, time.min, tzinfo=workspace_tz)
    window_end = datetime.combine(planning_end + timedelta(days=1), time.min, tzinfo=workspace_tz)
    existing_daily_counts = defaultdict(int)
    for scheduled_at in PlatformPost.objects.filter(
        social_account__in=accounts,
        scheduled_at__gte=window_start,
        scheduled_at__lt=window_end,
    ).values_list("scheduled_at", flat=True):
        existing_daily_counts[scheduled_at.astimezone(workspace_tz).date()] += 1
    for proposed_at in PlatformPost.objects.filter(
        social_account__in=accounts,
        scheduled_at__isnull=True,
        post__proposed_publish_at__gte=window_start,
        post__proposed_publish_at__lt=window_end,
    ).values_list("post__proposed_publish_at", flat=True):
        existing_daily_counts[proposed_at.astimezone(workspace_tz).date()] += 1
    insight_by_account = {insight["account"].id: insight for insight in insights}
    planned_counts = defaultdict(int)
    slot_cursors = defaultdict(int)
    planned_daily_counts = defaultdict(int)
    planned_targets = []
    planned_creators = []
    items = []
    per_account_soft_cap = math.ceil(count / max(1, len(accounts))) + 1
    remaining = list(candidates)
    while remaining and len(items) < count:
        choices = []
        for candidate in remaining:
            submission = candidate["submission"]
            for account in accounts:
                slots = slots_by_account[account.id]
                if planned_counts[account.id] >= per_account_soft_cap:
                    continue
                index = slot_cursors[account.id]
                while index < len(slots):
                    local_date = slots[index][0].astimezone(workspace_tz).date()
                    capacity = limits_by_date.get(local_date, daily_limit)
                    if existing_daily_counts[local_date] + planned_daily_counts[local_date] < capacity:
                        break
                    index += 1
                slot_cursors[account.id] = index
                if index >= len(slots):
                    continue
                allowed, _error = account_rights(submission, account)
                if not allowed or not _media_works_for_account(submission, account):
                    continue
                insight = insight_by_account[account.id]
                fit, reasons = _account_fit(candidate, insight, planned_targets, planned_creators)
                slot_at, pattern = slots[index]
                load_penalty = planned_counts[account.id] * 10
                choices.append(
                    (
                        candidate["base_score"] + fit - load_penalty,
                        candidate,
                        account,
                        slot_at,
                        pattern,
                        reasons,
                    )
                )
        if not choices:
            break
        _score, candidate, account, slot_at, pattern, reasons = max(choices, key=lambda item: item[0])
        remaining.remove(candidate)
        submission = candidate["submission"]
        planned_counts[account.id] += 1
        slot_cursors[account.id] += 1
        planned_daily_counts[slot_at.astimezone(workspace_tz).date()] += 1
        target_key = (submission.target_label or "").strip().casefold()
        creator_key = str(submission.creator_id or "")
        planned_targets.append(target_key)
        planned_creators.append(creator_key)
        if candidate["engagement_rank"] <= max(count * 2, 10):
            reasons.insert(0, f"engagement rank #{candidate['engagement_rank']}")
        if pattern["sample_count"]:
            reasons.append(f"learned from {pattern['sample_count']} recent posts in this window")
        else:
            reasons.append("uses the account's open posting rhythm")
        items.append(
            {
                "submission": submission,
                "account": account,
                "scheduled_at": slot_at,
                "engagement_label": _engagement_label(candidate["metrics"]),
                "engagement_rank": candidate["engagement_rank"],
                "reason": " · ".join(dict.fromkeys(reasons)),
                "format_key": candidate["format_key"],
                "winner_examples": [
                    row["caption"]
                    for row in history_by_account.get(account.id, [])
                    if row.get("caption") and row.get("relative_index") is not None
                ][:3],
            }
        )
    items.sort(key=lambda item: item["scheduled_at"])
    direct_schedule = workspace.approval_workflow_mode not in DIRECT_SCHEDULE_BLOCKED_MODES
    empty_reason = ""
    if not items:
        eligible_dates = [
            planning_start + timedelta(days=offset)
            for offset in range(days)
            if (planning_start + timedelta(days=offset)).weekday() in selected_weekdays
        ]
        if not selected_weekdays:
            empty_reason = "Select at least one posting day to build this schedule."
        elif not any(limits_by_date.get(day, daily_limit) > existing_daily_counts[day] for day in eligible_dates):
            empty_reason = "Every selected date is already at capacity. Increase a daily limit or extend the range."
        elif not any(slots_by_account.values()):
            empty_reason = (
                "No open posting windows match these dates and weekdays. Extend the range or select more days."
            )
        else:
            empty_reason = "No undrafted, quality-checked content has valid rights for the selected accounts."
    return {
        "items": items,
        "accounts": insights,
        "candidate_count": len(candidates),
        "requested_count": count,
        "requested_days": days,
        "start_date": planning_start,
        "end_date": planning_end,
        "daily_limit": daily_limit,
        "weekdays": selected_weekdays,
        "date_limits": limits_by_date,
        "daily_counts": {day: value for day, value in sorted(planned_daily_counts.items())},
        "existing_daily_counts": {day: value for day, value in sorted(existing_daily_counts.items())},
        "direct_schedule": direct_schedule,
        "empty_reason": empty_reason,
    }


def plan_payload(workspace, plan):
    return {
        "workspace_id": str(workspace.id),
        "constraints": {
            "start_date": plan["start_date"].isoformat(),
            "end_date": plan["end_date"].isoformat(),
            "weekdays": plan["weekdays"],
            "daily_limit": plan["daily_limit"],
            "date_limits": {day.isoformat(): limit for day, limit in plan["date_limits"].items()},
        },
        "items": [
            {
                "submission_id": str(item["submission"].id),
                "account_id": str(item["account"].id),
                "scheduled_at": item["scheduled_at"].isoformat(),
                "reason": item["reason"][:500],
            }
            for item in plan["items"]
        ],
    }


def _credit_line(submission):
    passport = submission.rights_passport
    if passport.credit_required and passport.credit_text:
        return f"Community content by {passport.credit_text}"
    if submission.contributor_handle:
        return f"Community content by @{submission.contributor_handle.lstrip('@')}"
    if submission.contributor_name:
        return f"Community content by {submission.contributor_name}"
    return "Community content"


def _caption_for_account(submission, account, *, lead_override=None):
    lead = str(lead_override).strip() if lead_override is not None else (submission.body or "").strip()
    if not lead and submission.title and submission.title != submission.target_label:
        lead = submission.title.strip()
    credit = _credit_line(submission)
    location = f"📍 {submission.target_label}" if submission.target_label else ""
    lead = re.sub(re.escape(credit), "", lead, flags=re.IGNORECASE).strip()
    if location:
        lead = re.sub(re.escape(location), "", lead, flags=re.IGNORECASE).strip()
    tail = [part for part in (credit, location) if part]
    suffix = "\n\n".join(tail)
    caption = "\n\n".join(part for part in (lead, suffix) if part)
    if account.caption_wire_length(caption) <= account.char_limit:
        return caption
    if account.caption_wire_length(suffix) > account.char_limit:
        raise SmartPlanError(f"Required creator credit is too long for {account.display_label}.")
    low, high = 0, len(lead)
    while low < high:
        midpoint = (low + high + 1) // 2
        shortened = lead[:midpoint].rstrip() + ("…" if midpoint < len(lead) else "")
        value = "\n\n".join(part for part in (shortened, suffix) if part)
        if account.caption_wire_length(value) <= account.char_limit:
            low = midpoint
        else:
            high = midpoint - 1
    shortened = lead[:low].rstrip() + ("…" if low < len(lead) else "")
    return "\n\n".join(part for part in (shortened, suffix) if part)


def _parse_slot(value):
    parsed = parse_datetime(str(value or ""))
    if parsed is None:
        raise SmartPlanError("The planned schedule is invalid. Refresh the plan and try again.")
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def commit_smart_plan(workspace, payload, *, actor, caption_overrides=None):
    if str(payload.get("workspace_id")) != str(workspace.id):
        raise SmartPlanError("This plan belongs to another workspace.")
    raw_items = list(payload.get("items") or [])
    if not raw_items or len(raw_items) > MAX_PLAN_COUNT:
        raise SmartPlanError("Refresh the Smart Plan before scheduling.")
    direct_schedule = workspace.approval_workflow_mode not in DIRECT_SCHEDULE_BLOCKED_MODES
    now = timezone.now()
    created = []
    with transaction.atomic():
        submissions = {
            str(item.id): item
            for item in UGCSubmission.objects.select_for_update()
            .filter(workspace=workspace, id__in=[row.get("submission_id") for row in raw_items])
            .select_related("creator", "media_asset", "rights_passport")
        }
        accounts = {
            str(item.id): item
            for item in SocialAccount.objects.select_for_update().filter(
                workspace=workspace,
                connection_status=SocialAccount.ConnectionStatus.CONNECTED,
                id__in=[row.get("account_id") for row in raw_items],
            )
        }
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
