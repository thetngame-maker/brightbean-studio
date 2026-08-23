"""Deterministic TN Game target coverage intelligence from Studio-owned data."""

from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from .models import UGCRightsPassport, UGCSubmission
from .ugc_creator_opportunities import creator_engagement_score
from .ugc_mobile_quality import approved_quality
from .ugc_permissions import GRANTED, get_permission
from .ugc_target_catalog import build_target_catalog

TN_WEST = -90.4
TN_EAST = -81.5
TN_SOUTH = 34.9
TN_NORTH = 36.75
STALE_AFTER_DAYS = 90

COVERAGE_LABELS = {
    "gap": "No coverage",
    "permission": "Needs permission",
    "thin": "Thin coverage",
    "stale": "Needs fresh content",
    "strong": "Strong coverage",
}


def _map_position(latitude, longitude):
    if latitude is None or longitude is None:
        return None
    if not (TN_SOUTH <= latitude <= TN_NORTH and TN_WEST <= longitude <= TN_EAST):
        return None
    x = 24 + ((longitude - TN_WEST) / (TN_EAST - TN_WEST) * 652)
    y = 18 + ((TN_NORTH - latitude) / (TN_NORTH - TN_SOUTH) * 124)
    return round(x, 1), round(y, 1)


def _coverage_state(item, *, now, stale_before):
    if item["content_count"] == 0:
        return "gap", "No community content has been captured for this target yet.", 100
    if item["granted_count"] == 0:
        return (
            "permission",
            f"{item['content_count']} captured post{'s' if item['content_count'] != 1 else ''}, but none have active reuse rights.",
            85 + min(14, item["content_count"]),
        )
    if item["latest_content_at"] and item["latest_content_at"] < stale_before:
        age = (now - item["latest_content_at"]).days
        return "stale", f"The newest captured content is {age} days old.", 70 + min(14, age // 30)
    if item["publishable_count"] < 2:
        return (
            "thin",
            f"Only {item['publishable_count']} clean, reusable asset{'s' if item['publishable_count'] != 1 else ''} are ready.",
            55 + max(0, 10 - item["publishable_count"] * 5),
        )
    return (
        "strong",
        f"{item['publishable_count']} clean reusable assets · {item['drafted_count']} drafted.",
        max(0, 30 - min(20, item["publishable_count"] * 3 + item["drafted_count"] * 2)),
    )


def build_coverage_map(workspace, *, now=None, limit=500):
    """Return one ranked coverage record per canonical target without provider calls."""
    now = now or timezone.now()
    stale_before = now - timedelta(days=STALE_AFTER_DAYS)
    targets = build_target_catalog(workspace, limit=limit)
    by_key = {}
    for target in targets:
        target.update(
            {
                "content_count": 0,
                "approved_count": 0,
                "granted_count": 0,
                "publishable_count": 0,
                "drafted_count": 0,
                "needs_check_count": 0,
                "prospect_count": 0,
                "creator_ids": set(),
                "engagement_score": 0,
                "latest_content_at": None,
            }
        )
        by_key[(target["target_type"], target["target_id"])] = target

    submissions = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .exclude(status__in=[UGCSubmission.Status.REJECTED, UGCSubmission.Status.REMOVED])
        .select_related("rights_passport")
        .only(
            "id",
            "target_type",
            "target_id",
            "status",
            "creator_id",
            "metadata",
            "target_label",
            "title",
            "body",
            "submitted_at",
            "rights_passport__status",
            "rights_passport__allow_organic_social",
            "rights_passport__expires_at",
        )
    )
    for submission in submissions.iterator(chunk_size=250):
        item = by_key.get((submission.target_type, submission.target_id))
        if item is None:
            continue
        item["content_count"] += 1
        item["engagement_score"] += creator_engagement_score([submission.metadata])
        if submission.creator_id:
            item["creator_ids"].add(submission.creator_id)
        if item["latest_content_at"] is None or submission.submitted_at > item["latest_content_at"]:
            item["latest_content_at"] = submission.submitted_at
        permission = get_permission(submission.metadata)
        if permission.get("status") != GRANTED:
            item["prospect_count"] += 1

        try:
            passport = submission.rights_passport
        except (AttributeError, ObjectDoesNotExist, UGCRightsPassport.DoesNotExist):
            passport = None
        rights_active = bool(passport and passport.is_active and passport.allow_organic_social)
        if rights_active:
            item["granted_count"] += 1
        if submission.status != UGCSubmission.Status.APPROVED:
            continue
        item["approved_count"] += 1
        drafted = bool((submission.metadata or {}).get("studio_post_ids"))
        if drafted:
            item["drafted_count"] += 1
        quality = approved_quality(submission)
        if quality["needs_check"]:
            item["needs_check_count"] += 1
        elif rights_active:
            item["publishable_count"] += 1

    counts = {key: 0 for key in COVERAGE_LABELS}
    mapped = []
    for index, item in enumerate(targets, start=1):
        item["creator_count"] = len(item.pop("creator_ids"))
        state, reason, priority = _coverage_state(item, now=now, stale_before=stale_before)
        item["coverage_state"] = state
        item["coverage_label"] = COVERAGE_LABELS[state]
        item["coverage_reason"] = reason
        item["priority_score"] = priority
        fresh_bonus = 20 if item["latest_content_at"] and item["latest_content_at"] >= now - timedelta(days=30) else 0
        item["coverage_score"] = min(
            100,
            item["publishable_count"] * 25 + item["granted_count"] * 5 + item["drafted_count"] * 10 + fresh_bonus,
        )
        item["anchor_id"] = f"target-{index}"
        counts[state] += 1
        position = _map_position(item.get("latitude"), item.get("longitude"))
        if position:
            item["map_x"], item["map_y"] = position
            mapped.append(item)

    targets.sort(key=lambda item: (-item["priority_score"], item["target_label"].lower()))
    return {
        "targets": targets,
        "counts": {"all": len(targets), **counts},
        "mapped_targets": mapped[:120],
        "mapped_count": len(mapped),
        "stale_after_days": STALE_AFTER_DAYS,
    }
