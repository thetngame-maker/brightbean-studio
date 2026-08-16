"""Comment reading shared by the two Instagram providers.

Both Instagram connections read the same ``/{media-id}/comments`` edge and
differ only in host — Facebook Login goes through ``graph.facebook.com``,
Instagram Login through ``graph.instagram.com``. Everything else (field sets,
cutoff arithmetic, the own-comment filter, the reply-target rules) is identical,
so it lives here once rather than being copy-pasted into both modules.

``FacebookProvider`` keeps its own versions: its comments edge answers with
``message`` rather than ``text``, its post ids need page-scoping first, and it
reads a ``/feed`` edge rather than ``/media``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from .types import InboxMessage

logger = logging.getLogger(__name__)

INSTAGRAM_MEDIA_SCAN_LIMIT = 25
INSTAGRAM_COMMENTS_PER_MEDIA = 25
INSTAGRAM_REPLIES_PER_COMMENT = 5
INSTAGRAM_COMMENT_PAGE_LIMIT = 4
INSTAGRAM_MEDIA_WINDOW_DAYS = 30
INSTAGRAM_COMMENT_LOOKBACK_HOURS = 24
INSTAGRAM_RECONCILE_COMMENT_LIMIT = 50

_ALWAYS_FIELDS = "id,text,timestamp,username"
_AUTHOR_FIELD = "from{id,username}"


def _field_set(*, author: bool, replies: bool) -> str:
    node = _ALWAYS_FIELDS + (f",{_AUTHOR_FIELD}" if author else "")
    if not replies:
        return node
    return f"{node},replies.limit({INSTAGRAM_REPLIES_PER_COMMENT}){{{node}}}"


INSTAGRAM_COMMENT_FIELD_SETS = (
    _field_set(author=True, replies=True),
    _field_set(author=True, replies=False),
    _field_set(author=False, replies=True),
    _field_set(author=False, replies=False),
)


def parse_graph_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("+0000", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _comment_text(comment: dict) -> str:
    return comment.get("text") or comment.get("message") or ""


def _comment_author(comment: dict) -> tuple[str, str]:
    author = comment.get("from") or {}
    author_id = str(author.get("id") or "")
    return author_id, str(author.get("username") or comment.get("username") or "")


def _same_handle(left: str, right: str) -> bool:
    return bool(left and right and left.lstrip("@").lower() == right.lstrip("@").lower())


def _is_own_comment(author_id: str, author_handle: str, owner_id: str, owner_handle: str) -> bool:
    if owner_id and author_id and author_id == owner_id:
        return True
    return _same_handle(author_handle, owner_handle)


def find_own_instagram_comment(
    request,
    *,
    api_base: str,
    access_token: str,
    media_id: str,
    text: str,
    own_id: str,
    own_handle: str = "",
) -> str | None:
    resp = request(
        "GET",
        f"{api_base}/{media_id}/comments",
        access_token=access_token,
        params={"fields": "id,text,from", "limit": INSTAGRAM_RECONCILE_COMMENT_LIMIT},
    )

    for comment in resp.json().get("data", []):
        author_id, author_handle = _comment_author(comment)
        if (author_id or author_handle) and not _is_own_comment(author_id, author_handle, own_id, own_handle):
            continue
        if _comment_text(comment) == text:
            return str(comment.get("id") or "")
    return None


def resolve_comment_reply_target(comment_id: str, extra: dict | None = None) -> tuple[str, str]:
    extra = extra or {}
    if extra.get("reply_edge") == "media":
        return str(comment_id), "comments"

    parent_id = str(extra.get("parent_id") or "")
    not_a_parent = {
        str(comment_id),
        str(extra.get("post_id") or ""),
        str(extra.get("stored_post_id") or ""),
        "",
    }
    if parent_id not in not_a_parent:
        return parent_id, "replies"
    return str(comment_id), "replies"


def fetch_instagram_comments(
    request,
    *,
    platform: str,
    host: str,
    media_url: str,
    access_token: str,
    since: datetime | None = None,
    owner_id: str = "",
    owner_handle: str = "",
) -> list[InboxMessage]:
    if not owner_id and not owner_handle:
        logger.warning("Skipping %s comment poll: no owner id or handle in credentials", platform)
        return []

    media_floor = datetime.now(UTC) - timedelta(days=INSTAGRAM_MEDIA_WINDOW_DAYS)
    media_items = _fetch_media(request, platform, media_url, access_token, media_floor)

    cutoff = None
    if since:
        cutoff = (since if since.tzinfo else since.replace(tzinfo=UTC)) - timedelta(
            hours=INSTAGRAM_COMMENT_LOOKBACK_HOURS
        )

    messages: list[InboxMessage] = []
    seen: set[str] = set()

    for media in media_items:
        try:
            comments = media.get("comments") or {}
            for comment in _iter_comments(request, host, access_token, comments):
                messages.extend(_comment_to_messages(comment, media, owner_id, owner_handle, cutoff, seen))
        except Exception as exc:
            logger.warning("Skipping %s comments for media %s: %s", platform, media.get("id"), exc)

    logger.debug("%s comment poll produced %d message(s)", platform, len(messages))
    return messages


def _fetch_media(request, platform: str, media_url: str, access_token: str, media_floor: datetime) -> list[dict]:
    last_error: Exception | None = None

    for fields in INSTAGRAM_COMMENT_FIELD_SETS:
        try:
            resp = request(
                "GET",
                media_url,
                access_token=access_token,
                params={
                    "fields": (f"id,timestamp,permalink,comments.limit({INSTAGRAM_COMMENTS_PER_MEDIA}){{{fields}}}"),
                    "limit": INSTAGRAM_MEDIA_SCAN_LIMIT,
                    "since": int(media_floor.timestamp()),
                },
            )
        except Exception as exc:
            if getattr(exc, "status_code", None) != 400:
                raise
            last_error = exc
            logger.info("%s rejected the comment field set; retrying with fewer fields", platform)
            continue
        return resp.json().get("data", [])

    raise last_error  # type: ignore[misc]


def _iter_comments(request, host: str, access_token: str, comments: dict):
    pages = 0
    while comments:
        yield from comments.get("data", [])

        next_url = (comments.get("paging") or {}).get("next")
        pages += 1
        if not next_url or pages >= INSTAGRAM_COMMENT_PAGE_LIMIT:
            if next_url:
                logger.debug("Instagram comment pagination capped at %d pages", INSTAGRAM_COMMENT_PAGE_LIMIT)
            return
        if urlparse(next_url).netloc != urlparse(host).netloc:
            logger.warning("Ignoring off-host Instagram comment paging URL")
            return
        comments = request("GET", next_url, access_token=access_token).json()


def _comment_to_messages(
    comment: dict,
    media: dict,
    owner_id: str,
    owner_handle: str,
    cutoff: datetime | None,
    seen: set[str],
) -> list[InboxMessage]:
    messages = []
    top = _to_message(comment, media, owner_id, owner_handle, cutoff, seen, parent_id="")
    if top is not None:
        messages.append(top)

    for reply in (comment.get("replies") or {}).get("data", []):
        message = _to_message(
            reply,
            media,
            owner_id,
            owner_handle,
            cutoff,
            seen,
            parent_id=str(comment.get("id") or ""),
        )
        if message is not None:
            messages.append(message)
    return messages


def _to_message(
    comment: dict,
    media: dict,
    owner_id: str,
    owner_handle: str,
    cutoff: datetime | None,
    seen: set[str],
    *,
    parent_id: str,
) -> InboxMessage | None:
    comment_id = str(comment.get("id") or "")
    if not comment_id or comment_id in seen:
        return None

    author_id, author_handle = _comment_author(comment)
    if _is_own_comment(author_id, author_handle, owner_id, owner_handle):
        return None

    timestamp = parse_graph_time(comment.get("timestamp"))
    if timestamp is None:
        logger.debug("Skipping Instagram comment %s with unparseable timestamp", comment_id)
        return None
    if cutoff and timestamp < cutoff:
        return None

    seen.add(comment_id)
    media_id = str(media.get("id") or "")
    media_type = str(media.get("media_type") or "")
    media_url = str(media.get("media_url") or "")
    thumbnail_url = str(media.get("thumbnail_url") or "")
    return InboxMessage(
        platform_message_id=comment_id,
        sender_id=author_id,
        sender_name=author_handle or "Instagram user",
        text=_comment_text(comment),
        timestamp=timestamp,
        message_type="comment",
        extra={
            "comment_id": comment_id,
            "post_id": media_id,
            "stored_post_id": media_id,
            "parent_id": parent_id,
            "post_permalink_url": media.get("permalink", ""),
            "post_caption": str(media.get("caption") or ""),
            "post_media_type": media_type,
            "post_media_url": media_url,
            "post_thumbnail_url": thumbnail_url,
            # Prefer the public username for display. The scoped numeric user ID
            # remains available separately as sender_id for API addressing.
            "sender_handle": author_handle or author_id,
            "reply_edge": "comment",
            "source": "poll",
        },
    )