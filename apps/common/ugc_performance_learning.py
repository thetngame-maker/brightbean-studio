"""Evidence-backed content lessons from Studio's existing post analytics."""

from collections import defaultdict
from datetime import timedelta

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

from apps.analytics.models import PostInsightsSnapshot
from apps.composer.models import PlatformPost

from .models import ContentPerformanceProfile, UGCSubmission

INTERACTION_KEYS = ("likes", "reactions", "comments", "replies", "shares", "reposts", "saves", "clicks", "outbound")
DENOMINATOR_KEYS = ("views", "reach", "impressions", "plays")
PROFILE_FIELDS = ("source_type", "opening_hook", "caption_style", "season", "subject")


def _ugc_post_map(workspace):
    result = {}
    submissions = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .select_related("creator")
        .only(
            "id",
            "creator_id",
            "target_type",
            "target_id",
            "target_label",
            "target_url",
            "metadata",
            "creator__display_name",
        )
    )
    for submission in submissions.iterator(chunk_size=250):
        post_ids = (submission.metadata or {}).get("studio_post_ids") or []
        if not isinstance(post_ids, list):
            continue
        for post_id in post_ids:
            if post_id:
                result[str(post_id)] = submission
    return result


def inferred_ugc_for_post(workspace, post_id):
    return _ugc_post_map(workspace).get(str(post_id))


def _latest_stats(platform_posts):
    post_ids = [item.id for item in platform_posts]
    result = defaultdict(dict)
    seen = set()
    rows = PostInsightsSnapshot.objects.filter(platform_post_id__in=post_ids).order_by(
        "platform_post_id", "metric_key", "-date"
    )
    for snapshot in rows:
        key = (snapshot.platform_post_id, snapshot.metric_key)
        if key in seen:
            continue
        seen.add(key)
        result[snapshot.platform_post_id][snapshot.metric_key] = float(snapshot.value or 0)
    return result


def _content_format(platform_post):
    attachments = list(platform_post.post.media_attachments.all())
    if len(attachments) > 1:
        return "carousel", "Carousel"
    if attachments:
        asset = attachments[0].media_asset
        if asset.is_video:
            return "video", "Reel / video"
        return "photo", "Photo"
    return "text", "Text"


def _metric_score(stats):
    interactions = sum(float(stats.get(key) or 0) for key in INTERACTION_KEYS)
    denominator = next((float(stats.get(key) or 0) for key in DENOMINATOR_KEYS if stats.get(key)), 0)
    if denominator > 0:
        return (interactions / denominator) * 100, interactions, denominator
    if interactions > 0:
        return interactions, interactions, 0
    return None, 0, denominator


def _profile_values(profile, inferred_submission):
    if profile is not None:
        creator = profile.creator or (profile.source_submission.creator if profile.source_submission_id else None)
        return {
            "source_type": profile.source_type,
            "opening_hook": profile.opening_hook,
            "caption_style": profile.caption_style,
            "season": profile.season,
            "subject": profile.subject,
            "target_type": profile.target_type,
            "target_id": profile.target_id,
            "target_label": profile.target_label,
            "target_url": profile.target_url,
            "creator": creator,
            "notes": profile.notes,
        }
    return {
        "source_type": ContentPerformanceProfile.SourceType.UGC if inferred_submission else "",
        "opening_hook": "",
        "caption_style": "",
        "season": "",
        "subject": "",
        "target_type": inferred_submission.target_type if inferred_submission else "",
        "target_id": inferred_submission.target_id if inferred_submission else "",
        "target_label": inferred_submission.target_label if inferred_submission else "",
        "target_url": inferred_submission.target_url if inferred_submission else "",
        "creator": inferred_submission.creator if inferred_submission else None,
        "notes": "",
    }


def _choice_label(choices, value):
    return dict(choices).get(value, value.replace("_", " ").title() if value else "")


def _lesson_candidates(rows):
    dimensions = (
        ("source_type", "Source"),
        ("format_key", "Format"),
        ("opening_hook", "Opening hook"),
        ("caption_style", "Caption style"),
        ("season", "Season"),
        ("subject", "Subject"),
        ("target_label", "TN target"),
        ("creator_label", "Creator"),
        ("daypart_key", "Posting time"),
        ("day_type_key", "Posting day"),
    )
    by_account = defaultdict(list)
    for row in rows:
        if row["relative_index"] is not None:
            by_account[row["account_id"]].append(row)
    lessons = []
    for account_rows in by_account.values():
        if len(account_rows) < 4:
            continue
        for field, dimension_label in dimensions:
            groups = defaultdict(list)
            for row in account_rows:
                if row.get(field):
                    groups[row[field]].append(row)
            for value, grouped in groups.items():
                if len(grouped) < 2 or len(grouped) == len(account_rows):
                    continue
                average = sum(row["relative_index"] for row in grouped) / len(grouped)
                lift = round(average - 100)
                if lift < 5:
                    continue
                label = grouped[0].get(f"{field}_label") or str(value)
                account_label = grouped[0]["account_label"]
                lessons.append(
                    {
                        "title": f"Use {label.lower()} more on {account_label}",
                        "evidence": (
                            f"{label} has performed {lift}% above this account’s baseline "
                            f"across {len(grouped)} published posts."
                        ),
                        "dimension": dimension_label,
                        "lift": lift,
                        "sample_count": len(grouped),
                    }
                )
    lessons.sort(key=lambda item: (-item["lift"], -item["sample_count"], item["title"]))
    return lessons[:6]


def build_performance_learning(workspace, *, days=90, limit=500):
    """Return published post rows and conservative within-account lessons."""
    queryset = (
        PlatformPost.objects.filter(
            social_account__workspace=workspace,
            status=PlatformPost.Status.PUBLISHED,
            published_at__isnull=False,
        )
        .select_related(
            "social_account",
            "post",
            "post__performance_profile",
            "post__performance_profile__source_submission",
            "post__performance_profile__creator",
        )
        .prefetch_related("post__media_attachments__media_asset")
        .order_by("-published_at")
    )
    if days is not None:
        queryset = queryset.filter(published_at__gte=timezone.now() - timedelta(days=days))
    platform_posts = list(queryset[:limit])
    stats_by_post = _latest_stats(platform_posts)
    ugc_by_post = _ugc_post_map(workspace)
    rows = []
    for platform_post in platform_posts:
        try:
            profile = platform_post.post.performance_profile
        except (AttributeError, ObjectDoesNotExist, ContentPerformanceProfile.DoesNotExist):
            profile = None
        inferred_submission = ugc_by_post.get(str(platform_post.post_id))
        values = _profile_values(profile, inferred_submission)
        stats = stats_by_post.get(platform_post.id, {})
        score, interactions, denominator = _metric_score(stats)
        format_key, format_label = _content_format(platform_post)
        local_published = timezone.localtime(platform_post.published_at)
        if local_published.hour < 12:
            daypart_key, daypart_label = "morning", "Morning posts"
        elif local_published.hour < 17:
            daypart_key, daypart_label = "afternoon", "Afternoon posts"
        else:
            daypart_key, daypart_label = "evening", "Evening posts"
        day_type_key = "weekend" if local_published.weekday() >= 5 else "weekday"
        creator = values["creator"]
        creator_label = creator.display_name if creator else ""
        source_submission = (
            profile.source_submission if profile and profile.source_submission_id else inferred_submission
        )
        audience_metric_key = next((key for key in DENOMINATOR_KEYS if stats.get(key)), "")
        audience_value = round(float(stats.get(audience_metric_key) or 0)) if audience_metric_key else 0
        row = {
            "platform_post": platform_post,
            "post": platform_post.post,
            "profile": profile,
            "source_submission": source_submission,
            "account_id": platform_post.social_account_id,
            "account_label": platform_post.social_account.account_name or platform_post.social_account.account_handle,
            "platform": platform_post.social_account.platform,
            "caption": platform_post.effective_caption,
            "stats": stats,
            "score": score,
            "interactions": round(interactions),
            "denominator": round(denominator),
            "audience_metric_key": audience_metric_key,
            "audience_metric_label": audience_metric_key.replace("_", " ").title()
            if audience_metric_key
            else "Exposure",
            "audience_value": audience_value,
            "share_save_count": round(sum(float(stats.get(key) or 0) for key in ("shares", "reposts", "saves"))),
            "engagement_rate": round(score, 2) if denominator and score is not None else None,
            "relative_index": None,
            "format_key": format_key,
            "format_key_label": format_label,
            "daypart_key": daypart_key,
            "daypart_key_label": daypart_label,
            "day_type_key": day_type_key,
            "day_type_key_label": "Weekend posts" if day_type_key == "weekend" else "Weekday posts",
            "creator_label": creator_label,
            "creator_label_label": creator_label,
            **values,
        }
        row["source_type_label"] = _choice_label(ContentPerformanceProfile.SourceType.choices, row["source_type"])
        row["opening_hook_label"] = _choice_label(ContentPerformanceProfile.OpeningHook.choices, row["opening_hook"])
        row["caption_style_label"] = _choice_label(ContentPerformanceProfile.CaptionStyle.choices, row["caption_style"])
        row["season_label"] = _choice_label(ContentPerformanceProfile.Season.choices, row["season"])
        row["subject_label"] = _choice_label(ContentPerformanceProfile.Subject.choices, row["subject"])
        row["target_label_label"] = row["target_label"]
        row["tagged_count"] = sum(bool(row[field]) for field in PROFILE_FIELDS)
        row["needs_tags"] = row["tagged_count"] < len(PROFILE_FIELDS)
        rows.append(row)

    account_scores = defaultdict(list)
    for row in rows:
        if row["score"] is not None:
            account_scores[row["account_id"]].append(row["score"])
    account_averages = {
        account_id: sum(scores) / len(scores) for account_id, scores in account_scores.items() if scores
    }
    for row in rows:
        average = account_averages.get(row["account_id"])
        if row["score"] is not None and average:
            row["relative_index"] = round((row["score"] / average) * 100, 1)

    return {
        "rows": rows,
        "lessons": _lesson_candidates(rows),
        "counts": {
            "published": len(rows),
            "with_analytics": sum(row["score"] is not None for row in rows),
            "tagged": sum(not row["needs_tags"] for row in rows),
            "needs_tags": sum(row["needs_tags"] for row in rows),
            "ugc": sum(row["source_type"] == ContentPerformanceProfile.SourceType.UGC for row in rows),
        },
        "limit_reached": len(platform_posts) == limit,
    }
