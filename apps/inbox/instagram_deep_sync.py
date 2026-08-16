"""Deeper Instagram inbox recovery poll.

This recovery pass intentionally keeps media pagination lightweight, then reads
comments from each media item's own ``/comments`` edge. Instagram is much more
reliable with this shape than with a deeply-expanded ``/media`` query carried
across several cursor pages.
"""

from __future__ import annotations

import logging
from datetime import UTC, timedelta
from urllib.parse import urlparse

from background_task import background
from django.utils import timezone

from apps.publisher.engine import _resolve_publish_credentials
from apps.social_accounts.models import SocialAccount
from providers import get_provider
from providers.meta_comments import (
    INSTAGRAM_COMMENT_FIELD_SETS,
    INSTAGRAM_COMMENTS_PER_MEDIA,
    INSTAGRAM_MEDIA_SCAN_LIMIT,
    _comment_to_messages,
    _iter_comments,
)

from .models import InboxMessage, InboxReply
from .tasks import InboxSyncEngine, _normalize_instagram_mention, _related_post_key, resolve_related_posts

logger = logging.getLogger(__name__)

INSTAGRAM_DEEP_MEDIA_PAGE_LIMIT = 4
INSTAGRAM_DEEP_MEDIA_WINDOW_DAYS = 90
INSTAGRAM_DEEP_SYNC_INTERVAL_SECONDS = 5 * 60
INSTAGRAM_DEEP_COMMENT_LOOKBACK_HOURS = 24


def _endpoints(account):
    provider = get_provider(account.platform, _resolve_publish_credentials(account))
    if account.platform == "instagram_login":
        from providers.instagram_login import API_BASE

        return provider, f"{API_BASE}/me/media", API_BASE
    if account.platform == "instagram":
        from providers.instagram import BASE_URL

        return provider, f"{BASE_URL}/{account.account_platform_id}/media", BASE_URL
    raise ValueError(f"Unsupported platform for Instagram deep sync: {account.platform}")


def _fetch_media_pages(provider, media_url: str, host: str, access_token: str) -> list[dict]:
    """Fetch up to four pages of recent media without nested comment expansion.

    Later-page failures are fail-soft: everything fetched before the failure is
    still useful recovery data and is processed normally.
    """
    media_floor = timezone.now().astimezone(UTC) - timedelta(days=INSTAGRAM_DEEP_MEDIA_WINDOW_DAYS)
    response = provider._request(
        "GET",
        media_url,
        access_token=access_token,
        params={
            "fields": "id,timestamp,permalink,caption,media_type,media_url,thumbnail_url",
            "limit": INSTAGRAM_MEDIA_SCAN_LIMIT,
            "since": int(media_floor.timestamp()),
        },
    )

    media: list[dict] = []
    payload = response.json()
    pages = 0

    while payload and pages < INSTAGRAM_DEEP_MEDIA_PAGE_LIMIT:
        media.extend(payload.get("data", []))
        pages += 1
        next_url = (payload.get("paging") or {}).get("next")
        if not next_url or pages >= INSTAGRAM_DEEP_MEDIA_PAGE_LIMIT:
            break
        if urlparse(next_url).netloc != urlparse(host).netloc:
            logger.warning("Ignoring off-host Instagram media paging URL")
            break
        try:
            payload = provider._request("GET", next_url, access_token=access_token).json()
        except Exception as exc:
            logger.warning(
                "Instagram deep media pagination stopped after %d page(s): %s",
                pages,
                exc,
            )
            break

    return media


def _fetch_media_comments(provider, host: str, access_token: str, media_id: str) -> dict:
    """Read one media item's comments, degrading optional fields on HTTP 400."""
    last_error: Exception | None = None

    for fields in INSTAGRAM_COMMENT_FIELD_SETS:
        try:
            response = provider._request(
                "GET",
                f"{host}/{media_id}/comments",
                access_token=access_token,
                params={
                    "fields": fields,
                    "limit": INSTAGRAM_COMMENTS_PER_MEDIA,
                },
            )
            return response.json()
        except Exception as exc:
            if getattr(exc, "status_code", None) != 400:
                raise
            last_error = exc
            logger.debug(
                "Instagram deep comment field set rejected for media %s; trying a smaller set",
                media_id,
            )

    raise last_error  # type: ignore[misc]


def sync_instagram_account_deep(account) -> int:
    """Deep-poll one Instagram account and return newly created inbox rows."""
    provider, media_url, host = _endpoints(account)
    access_token = account.oauth_access_token

    media_items = _fetch_media_pages(provider, media_url, host, access_token)

    last_received = (
        InboxMessage.objects.filter(social_account=account)
        .order_by("-received_at")
        .values_list("received_at", flat=True)
        .first()
    )
    cutoff = None
    if last_received:
        if timezone.is_naive(last_received):
            last_received = timezone.make_aware(last_received, timezone.get_default_timezone())
        cutoff = last_received - timedelta(hours=INSTAGRAM_DEEP_COMMENT_LOOKBACK_HOURS)

    messages = []
    seen: set[str] = set()

    for media in media_items:
        media_id = str(media.get("id") or "")
        if not media_id:
            continue
        try:
            comments = _fetch_media_comments(provider, host, access_token, media_id)
            for comment in _iter_comments(provider._request, host, access_token, comments):
                messages.extend(
                    _comment_to_messages(
                        comment,
                        media,
                        str(account.account_platform_id or ""),
                        str(account.account_handle or ""),
                        cutoff,
                        seen,
                    )
                )
        except Exception as exc:
            # One unreadable media item should never fail the recovery pass for
            # every other post. Log it and continue with the remaining media.
            logger.warning("Instagram deep sync skipped media %s: %s", media_id, exc)

    if not messages:
        return 0

    related_posts = resolve_related_posts(account, messages)
    outbound_reply_ids = set(
        InboxReply.objects.filter(inbox_message__social_account=account)
        .exclude(platform_reply_id="")
        .values_list("platform_reply_id", flat=True)
    )

    before = InboxMessage.objects.filter(social_account=account).count()
    engine = InboxSyncEngine()

    for msg in messages:
        platform_message_id = str(msg.platform_message_id or "")
        if platform_message_id and platform_message_id in outbound_reply_ids:
            continue
        msg = _normalize_instagram_mention(account, msg)
        engine._upsert_message(
            account,
            msg,
            notify=True,
            related_post_id=related_posts.get(_related_post_key(msg.extra)),
        )

    after = InboxMessage.objects.filter(social_account=account).count()
    return max(0, after - before)


def sync_all_instagram_deep() -> int:
    total = 0
    accounts = SocialAccount.objects.filter(
        platform__in=("instagram_login", "instagram"),
        connection_status__in=(
            SocialAccount.ConnectionStatus.CONNECTED,
            SocialAccount.ConnectionStatus.TOKEN_EXPIRING,
        ),
    )
    for account in accounts:
        try:
            total += sync_instagram_account_deep(account)
        except Exception:
            logger.exception("Instagram deep inbox sync failed for account %s", account.id)
    return total


@background(schedule=0)
def run_instagram_deep_sync_cycle():
    sync_all_instagram_deep()
