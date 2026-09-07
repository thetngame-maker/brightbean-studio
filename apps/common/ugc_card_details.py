"""Current engagement and account-level usage for Community Content cards."""

from uuid import UUID

from django.db.models import Q

from apps.composer.models import Post


def count_value(value):
    try:
        value = int(value)
        return value if value >= 0 else None
    except (ValueError, TypeError):
        return None


def engagement(submission):
    discovery = (submission.metadata or {}).get("discovery_import") or {}
    return {
        name: count_value(discovery.get(key))
        for name, key in (("likes", "like_count"), ("comments", "comment_count"), ("views", "view_count"))
    }


def engagement_score(submission):
    counts = engagement(submission)
    return (counts["likes"] or 0) + (counts["comments"] or 0) * 10 + (counts["views"] or 0) / 100


def decorate_cards(submissions, workspace):
    recorded = {}
    for item in submissions:
        ids = set()
        for value in (item.metadata or {}).get("studio_post_ids", []) or []:
            try:
                ids.add(UUID(str(value)))
            except (ValueError, TypeError):
                continue
        recorded[item.id] = ids
        item.engagement = engagement(item)
        item.studio_posts = []
    all_ids = set().union(*recorded.values()) if recorded else set()
    asset_ids = {item.media_asset_id for item in submissions if item.media_asset_id}
    posts = list(
        Post.objects.filter(workspace=workspace)
        .filter(
            Q(id__in=all_ids)
            | Q(performance_profile__source_submission_id__in=recorded)
            | Q(media_attachments__media_asset_id__in=asset_ids)
        )
        .distinct()
        .select_related("performance_profile")
        .prefetch_related("platform_posts__social_account", "media_attachments")
        .order_by("-created_at")
    )
    for post in posts:
        source_id = getattr(getattr(post, "performance_profile", None), "source_submission_id", None)
        media_ids = {attachment.media_asset_id for attachment in post.media_attachments.all()}
        rows = list(post.platform_posts.all())
        for item in submissions:
            if (
                post.id in recorded[item.id]
                or source_id == item.id
                or (item.media_asset_id and item.media_asset_id in media_ids)
            ):
                item.studio_posts.append({"id": post.id, "title": post.title or "Untitled post", "accounts": rows})
    for item in submissions:
        item.studio_usage_count = len(item.studio_posts)
