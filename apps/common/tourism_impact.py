"""Stable partner-facing tourism impact snapshots from Studio-owned data."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.analytics.models import PostInsightsSnapshot
from apps.composer.models import PlatformPost

from .models import (
    CampaignAttributionClick,
    CampaignAttributionConversion,
    CampaignAttributionLink,
    ContentPerformanceProfile,
    UGCContentMission,
    UGCCreatorCollaboration,
    UGCRightsPassport,
    UGCSubmission,
)
from .ugc_content_missions import decorate_content_mission
from .ugc_coverage import build_coverage_map
from .ugc_performance_learning import _ugc_post_map
from .ugc_target_catalog import build_target_catalog

EXPOSURE_KEYS = ("reach", "impressions", "views", "plays")
INTERACTION_KEYS = ("likes", "reactions", "comments", "replies", "shares", "reposts", "saves")
SHARE_SAVE_KEYS = ("shares", "reposts", "saves")
OUTBOUND_KEYS = ("clicks", "outbound")


def _bounds(workspace, start_date, end_date):
    try:
        zone = ZoneInfo(workspace.effective_timezone or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        zone = ZoneInfo("UTC")
    start = datetime.combine(start_date, time.min, tzinfo=zone)
    end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def _latest_stats(platform_posts, end_date):
    ids = [item.id for item in platform_posts]
    result = defaultdict(dict)
    seen = set()
    rows = PostInsightsSnapshot.objects.filter(platform_post_id__in=ids, date__lte=end_date).order_by(
        "platform_post_id", "metric_key", "-date"
    )
    for snapshot in rows.iterator(chunk_size=500):
        key = (snapshot.platform_post_id, snapshot.metric_key)
        if key in seen:
            continue
        seen.add(key)
        result[snapshot.platform_post_id][snapshot.metric_key] = float(snapshot.value or 0)
    return result


def _profile(post):
    try:
        return post.performance_profile
    except (AttributeError, ObjectDoesNotExist, ContentPerformanceProfile.DoesNotExist):
        return None


def _attribution(post, ugc_by_post):
    profile = _profile(post)
    submission = (
        profile.source_submission if profile and profile.source_submission_id else ugc_by_post.get(str(post.id))
    )
    creator = profile.creator if profile and profile.creator_id else (submission.creator if submission else None)
    return {
        "target_type": profile.target_type if profile else (submission.target_type if submission else ""),
        "target_id": profile.target_id if profile else (submission.target_id if submission else ""),
        "target_label": profile.target_label if profile else (submission.target_label if submission else ""),
        "source_type": profile.source_type
        if profile and profile.source_type
        else (ContentPerformanceProfile.SourceType.UGC if submission else ""),
        "creator": creator,
    }


def _target_match(attribution, target):
    if not target:
        return True
    return attribution["target_type"] == target["target_type"] and attribution["target_id"] == target["target_id"]


def _sum(stats, keys):
    return round(sum(float(stats.get(key) or 0) for key in keys))


def _exposure(stats):
    key = next((key for key in EXPOSURE_KEYS if stats.get(key)), "")
    return round(float(stats.get(key) or 0)) if key else 0, key


def _rights_assets(workspace, target):
    now = timezone.now()
    passports = (
        UGCRightsPassport.objects.for_workspace(workspace.id)
        .filter(
            status=UGCRightsPassport.Status.GRANTED,
            allow_organic_social=True,
            submission__status=UGCSubmission.Status.APPROVED,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    )
    if target:
        passports = passports.filter(
            submission__target_type=target["target_type"], submission__target_id=target["target_id"]
        )
    return passports.count()


def _mission_snapshot(workspace, target, period_start, period_end):
    missions = (
        UGCContentMission.objects.for_workspace(workspace.id)
        .filter(
            starts_at__date__lte=period_end,
        )
        .filter(Q(due_at__isnull=True) | Q(due_at__date__gte=period_start))
    )
    if target:
        missions = missions.filter(target_type=target["target_type"], target_id=target["target_id"])
    rows = []
    for mission in missions.order_by("-updated_at")[:50]:
        decorate_content_mission(mission)
        rows.append(
            {
                "title": mission.title,
                "target_label": mission.target_label,
                "status": mission.status,
                "status_label": mission.get_status_display(),
                "goal_count": mission.goal_count,
                "ready_count": mission.ready_count,
                "capture_count": mission.capture_count,
                "creator_count": mission.creator_count,
                "goal_met": mission.goal_met,
            }
        )
    return rows


def _attribution_snapshot(workspace, target, period_start, period_end, start_at, end_at):
    links = CampaignAttributionLink.objects.for_workspace(workspace.id)
    if target:
        links = links.filter(target_type=target["target_type"], target_id=target["target_id"])
    link_rows = list(
        links.values("id", "name", "code", "target_type", "target_id", "target_label", "utm_campaign")[:500]
    )
    link_ids = [row["id"] for row in link_rows]
    clicks = {
        row["link_id"]: row
        for row in CampaignAttributionClick.objects.filter(
            link_id__in=link_ids,
            day__gte=period_start,
            day__lte=period_end,
        )
        .values("link_id")
        .annotate(clicks=Sum("clicks"), unique_daily_visitors=Count("id"))
    }
    registrations = {
        row["link_id"]: row
        for row in CampaignAttributionConversion.objects.filter(
            link_id__in=link_ids,
            occurred_at__gte=start_at,
            occurred_at__lt=end_at,
        )
        .values("link_id")
        .annotate(registrations=Sum("quantity"))
    }
    campaigns = []
    total_clicks = 0
    total_visitors = 0
    total_registrations = 0
    for link in link_rows:
        click_row = clicks.get(link["id"], {})
        registration_row = registrations.get(link["id"], {})
        raw_clicks = int(click_row.get("clicks") or 0)
        visitors = int(click_row.get("unique_daily_visitors") or 0)
        registration_count = int(registration_row.get("registrations") or 0)
        if not (raw_clicks or registration_count):
            continue
        total_clicks += raw_clicks
        total_visitors += visitors
        total_registrations += registration_count
        campaigns.append(
            {
                "name": link["name"],
                "code": link["code"],
                "target_type": link["target_type"],
                "target_id": link["target_id"],
                "target_label": link["target_label"],
                "utm_campaign": link["utm_campaign"],
                "tracked_clicks": raw_clicks,
                "tracked_visits": visitors,
                "registrations": registration_count,
                "conversion_rate": round((registration_count / visitors) * 100, 1) if visitors else None,
            }
        )
    campaigns.sort(key=lambda row: (-row["registrations"], -row["tracked_visits"], row["name"]))
    return {
        "tracked_link_clicks": total_clicks,
        "tracked_website_visits": total_visitors,
        "tracked_registrations": total_registrations,
        "tracked_conversion_rate": round((total_registrations / total_visitors) * 100, 1)
        if total_visitors
        else None,
        "campaigns": campaigns[:20],
    }


def build_impact_snapshot(workspace, *, period_start, period_end, target=None, equivalent_cpm=Decimal("12")):
    """Build an explainable snapshot without fetching provider or TN Game APIs."""
    start_at, end_at = _bounds(workspace, period_start, period_end)
    platform_posts = list(
        PlatformPost.objects.filter(
            social_account__workspace=workspace,
            status=PlatformPost.Status.PUBLISHED,
            published_at__gte=start_at,
            published_at__lt=end_at,
        )
        .select_related(
            "social_account",
            "post",
            "post__performance_profile",
            "post__performance_profile__source_submission",
            "post__performance_profile__source_submission__creator",
            "post__performance_profile__creator",
        )
        .order_by("-published_at")[:1000]
    )
    ugc_by_post = _ugc_post_map(workspace)
    attributed = []
    for platform_post in platform_posts:
        attribution = _attribution(platform_post.post, ugc_by_post)
        if _target_match(attribution, target):
            attributed.append((platform_post, attribution))
    stats_by_post = _latest_stats([item[0] for item in attributed], period_end)

    totals = {
        "published_posts": len(attributed),
        "posts_with_analytics": 0,
        "measured_exposure": 0,
        "interactions": 0,
        "shares_saves": 0,
        "outbound_clicks": 0,
        "community_posts_published": 0,
    }
    target_rows = {}
    type_rows = defaultdict(lambda: {"published_posts": 0, "measured_exposure": 0, "interactions": 0})
    creator_ids = set()
    top_posts = []
    for platform_post, attribution in attributed:
        stats = stats_by_post.get(platform_post.id, {})
        exposure, exposure_key = _exposure(stats)
        interactions = _sum(stats, INTERACTION_KEYS)
        shares_saves = _sum(stats, SHARE_SAVE_KEYS)
        outbound = _sum(stats, OUTBOUND_KEYS)
        if stats:
            totals["posts_with_analytics"] += 1
        totals["measured_exposure"] += exposure
        totals["interactions"] += interactions
        totals["shares_saves"] += shares_saves
        totals["outbound_clicks"] += outbound
        if attribution["source_type"] == ContentPerformanceProfile.SourceType.UGC:
            totals["community_posts_published"] += 1
        if attribution["creator"]:
            creator_ids.add(str(attribution["creator"].id))
        target_key = (attribution["target_type"], attribution["target_id"])
        if all(target_key):
            row = target_rows.setdefault(
                target_key,
                {
                    "target_type": attribution["target_type"],
                    "target_id": attribution["target_id"],
                    "target_label": attribution["target_label"] or attribution["target_id"],
                    "published_posts": 0,
                    "measured_exposure": 0,
                    "interactions": 0,
                    "shares_saves": 0,
                    "community_posts": 0,
                    "creator_ids": set(),
                },
            )
            row["published_posts"] += 1
            row["measured_exposure"] += exposure
            row["interactions"] += interactions
            row["shares_saves"] += shares_saves
            if attribution["source_type"] == ContentPerformanceProfile.SourceType.UGC:
                row["community_posts"] += 1
            if attribution["creator"]:
                row["creator_ids"].add(str(attribution["creator"].id))
            type_row = type_rows[attribution["target_type"]]
            type_row["published_posts"] += 1
            type_row["measured_exposure"] += exposure
            type_row["interactions"] += interactions
        top_posts.append(
            {
                "title": platform_post.post.title or platform_post.effective_caption[:80] or "Published post",
                "account_label": platform_post.social_account.display_label,
                "published_at": platform_post.published_at.isoformat(),
                "target_label": attribution["target_label"],
                "source_type": attribution["source_type"],
                "measured_exposure": exposure,
                "exposure_metric": exposure_key.replace("_", " ").title() if exposure_key else "",
                "interactions": interactions,
                "shares_saves": shares_saves,
            }
        )

    submissions = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(
            submitted_at__gte=start_at,
            submitted_at__lt=end_at,
        )
        .exclude(status__in=[UGCSubmission.Status.REJECTED, UGCSubmission.Status.REMOVED])
    )
    if target:
        submissions = submissions.filter(target_type=target["target_type"], target_id=target["target_id"])
    contribution_count = submissions.count()
    creator_ids.update(
        str(value) for value in submissions.exclude(creator_id=None).values_list("creator_id", flat=True)
    )

    collaborations = UGCCreatorCollaboration.objects.for_workspace(workspace.id).filter(
        status=UGCCreatorCollaboration.Status.COMPLETED,
        completed_at__gte=start_at,
        completed_at__lt=end_at,
    )
    if target:
        collaborations = collaborations.filter(target_type=target["target_type"], target_id=target["target_id"])
    creator_ids.update(str(value) for value in collaborations.values_list("creator_id", flat=True))

    catalog = build_target_catalog(workspace, limit=500)
    published_keys = set(target_rows)
    if target:
        destination_total = 1
        destination_covered = int((target["target_type"], target["target_id"]) in published_keys)
    else:
        catalog_keys = {(item["target_type"], item["target_id"]) for item in catalog}
        destination_total = len(catalog_keys)
        destination_covered = len(published_keys & catalog_keys)
    coverage_percent = round((destination_covered / destination_total) * 100) if destination_total else 0

    coverage = build_coverage_map(workspace, limit=500)
    gaps = [
        {
            "target_type": item["target_type"],
            "target_id": item["target_id"],
            "target_label": item["target_label"],
            "coverage_state": item["coverage_state"],
            "coverage_label": item["coverage_label"],
            "coverage_reason": item["coverage_reason"],
            "priority_score": item["priority_score"],
        }
        for item in coverage["targets"]
        if item["coverage_state"] != "strong"
        and (not target or (item["target_type"] == target["target_type"] and item["target_id"] == target["target_id"]))
    ][:8]

    target_breakdown = []
    for row in target_rows.values():
        row["creator_count"] = len(row.pop("creator_ids"))
        target_breakdown.append(row)
    target_breakdown.sort(key=lambda row: (-row["measured_exposure"], -row["interactions"], row["target_label"]))
    type_breakdown = [
        {"target_type": key, "target_type_label": key.replace("_", " ").title(), **values}
        for key, values in type_rows.items()
    ]
    type_breakdown.sort(key=lambda row: (-row["measured_exposure"], row["target_type_label"]))
    top_posts.sort(key=lambda row: (-row["measured_exposure"], -row["interactions"], row["title"]))

    missions = _mission_snapshot(workspace, target, period_start, period_end)
    attribution = _attribution_snapshot(workspace, target, period_start, period_end, start_at, end_at)
    rights_assets = _rights_assets(workspace, target)
    estimated_value = round((totals["measured_exposure"] / 1000) * float(equivalent_cpm), 2)
    highlights = []
    if totals["published_posts"]:
        highlights.append(
            f"Published {totals['published_posts']} posts with {totals['measured_exposure']:,} measured exposure."
        )
    if contribution_count or rights_assets:
        highlights.append(
            f"Community participation added {contribution_count} contributions and left {rights_assets} rights-cleared assets ready for reuse."
        )
    if destination_total:
        highlights.append(
            f"Published coverage reached {destination_covered} of {destination_total} known TN Game destinations in this report scope."
        )
    if attribution["tracked_website_visits"] or attribution["tracked_registrations"]:
        highlights.append(
            f"First-party campaign links recorded {attribution['tracked_website_visits']:,} unique daily visits "
            f"and {attribution['tracked_registrations']:,} TN Game registrations."
        )

    return {
        "version": 2,
        "generated_at": timezone.now().isoformat(),
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "target": target or {},
        "totals": {
            **totals,
            "creator_participants": len(creator_ids),
            "community_contributions": contribution_count,
            "rights_cleared_assets": rights_assets,
            "completed_collaborations": collaborations.count(),
            "missions": len(missions),
            "missions_goal_met": sum(row["goal_met"] for row in missions),
            "destinations_covered": destination_covered,
            "destinations_total": destination_total,
            "coverage_percent": coverage_percent,
            "estimated_organic_value": estimated_value,
            "tracked_link_clicks": attribution["tracked_link_clicks"],
            "tracked_website_visits": attribution["tracked_website_visits"],
            "tracked_registrations": attribution["tracked_registrations"],
            "tracked_conversion_rate": attribution["tracked_conversion_rate"],
        },
        "equivalent_cpm": float(equivalent_cpm),
        "methodology": (
            "Measured exposure uses the latest stored reach, impressions, views, or plays snapshot for each post "
            "on or before the report end date. Equivalent media value multiplies measured exposure by the report’s "
            "editable CPM assumption. First-party website visits count anonymous unique visitor-days from Studio "
            "attribution links; TN Game registrations come from idempotent conversion-ledger entries. Known social "
            "preview bots are excluded, and no raw visitor identifiers are retained. No live provider or TN Game API "
            "calls are made while generating this report."
        ),
        "highlights": highlights,
        "target_breakdown": target_breakdown[:20],
        "target_type_breakdown": type_breakdown,
        "top_posts": top_posts[:8],
        "missions": missions[:8],
        "coverage_gaps": gaps,
        "campaign_attribution": attribution["campaigns"],
        "data_freshness": {
            "posts_with_analytics": totals["posts_with_analytics"],
            "published_posts": totals["published_posts"],
        },
    }
