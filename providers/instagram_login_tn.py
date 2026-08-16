"""TN Social Studio compatibility fixes for Instagram Login publishing and inbox.

Keep TN-specific production fixes isolated from the upstream provider so the
core BrightBean provider remains easy to compare/update.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .instagram_login import API_BASE, InstagramLoginProvider
from .meta_comments import (
    INSTAGRAM_COMMENTS_PER_MEDIA,
    INSTAGRAM_COMMENT_FIELD_SETS,
    INSTAGRAM_COMMENT_LOOKBACK_HOURS,
    INSTAGRAM_MEDIA_SCAN_LIMIT,
    INSTAGRAM_MEDIA_WINDOW_DAYS,
    _comment_to_messages,
    _iter_comments,
)
from .types import InboxMessage, PostType, PublishContent, PublishResult

logger = logging.getLogger(__name__)


class TNInstagramLoginProvider(InstagramLoginProvider):
    """Instagram Login provider with TN Social Studio production safeguards."""

    @staticmethod
    def _content_is_video(content: PublishContent) -> bool:
        """Best-effort media-kind check that survives presigned URL query strings."""
        candidates: list[str] = []
        if content.media_files:
            candidates.append(content.media_files[0])
        if content.media_urls:
            candidates.append(urlsplit(content.media_urls[0]).path)
        return any(str(value).lower().endswith((".mp4", ".mov", ".m4v")) for value in candidates)

    def _publish_story(self, access_token: str, content: PublishContent) -> PublishResult:
        """Publish the first attached photo/video as an Instagram Story.

        Story containers take the media itself; unlike feed/reel containers we
        deliberately do not attach the composer caption to the Story payload.
        """
        if not content.media_urls:
            return super()._publish_single(access_token, content)

        payload: dict = {"media_type": "STORIES"}
        if self._content_is_video(content):
            payload["video_url"] = content.media_urls[0]
        else:
            payload["image_url"] = content.media_urls[0]

        container_id = self._create_container(access_token, payload)
        self._wait_for_container(access_token, container_id)
        return self._publish_container(access_token, container_id)

    def _publish_primary(self, access_token: str, content: PublishContent) -> PublishResult:
        """Publish the requested primary feed/reel/story item."""
        if content.post_type == PostType.STORY:
            return self._publish_story(access_token, content)

        # The publisher engine intentionally resolves a lone video asset to
        # PostType.VIDEO when there is no explicit per-platform post_type hint.
        # Instagram no longer publishes standalone feed videos; they must use
        # the REELS container flow. The legacy Facebook-login Instagram provider
        # already handles VIDEO this way, but the direct Instagram Login provider
        # only checked REEL and therefore sent the video URL as image_url.
        if content.post_type == PostType.VIDEO:
            payload: dict = {
                "media_type": "REELS",
                "video_url": content.media_urls[0],
            }
            if content.text:
                payload["caption"] = content.text

            container_id = self._create_container(access_token, payload)
            self._wait_for_container(access_token, container_id)
            return self._publish_container(access_token, container_id)

        return super()._publish_single(access_token, content)

    def _publish_single(self, access_token: str, content: PublishContent) -> PublishResult:
        primary = self._publish_primary(access_token, content)

        # "Also add to Story" is intentionally secondary. If the feed/Reel has
        # already published and the Story copy fails, raising here would send the
        # whole PlatformPost through the normal retry path and duplicate the main
        # post. Preserve the successful primary result and record the Story error
        # in result.extra instead; the composer/publish log can surface it without
        # risking a duplicate feed post.
        also_story = bool((content.extra or {}).get("also_story"))
        if not also_story or content.post_type == PostType.STORY:
            return primary

        extra = dict(primary.extra or {})
        try:
            story = self._publish_story(access_token, content)
        except Exception as exc:  # secondary publish must never retry the primary
            logger.exception("Instagram primary published but Story copy failed")
            extra["story_copy_status"] = "failed"
            extra["story_copy_error"] = str(exc)[:1000]
        else:
            extra["story_copy_status"] = "published"
            extra["story_id"] = story.platform_post_id
            if story.url:
                extra["story_url"] = story.url

        return PublishResult(
            platform_post_id=primary.platform_post_id,
            url=primary.url,
            extra=extra,
        )

    # ------------------------------------------------------------------
    # Inbox comment polling
    # ------------------------------------------------------------------

    def _fetch_media_comments(self, access_token: str, since: datetime | None = None) -> list[InboxMessage]:
        """Poll recent Instagram comments using explicit per-media comment edges.

        BrightBean's shared fallback expands ``comments{...}`` inside ``/me/media``.
        Direct Instagram Login can successfully return the media list while
        refusing or omitting that nested expansion, which leaves the poll with
        zero comments even though DMs work. Fetching media first and then calling
        ``/{media-id}/comments`` is more reliable and also isolates a bad media
        item so it cannot suppress every other comment in the cycle.

        The media query also carries lightweight post context into each inbox
        comment so the UI can show what post the customer commented on without a
        new Graph request every time an agent opens the conversation.
        """
        owner_id = str(self.credentials.get("ig_user_id") or "")
        owner_handle = str(self.credentials.get("account_handle") or "")
        if not owner_id and not owner_handle:
            logger.warning("Skipping Instagram Login comment poll: no owner id or handle in credentials")
            return []

        media_floor = datetime.now(UTC) - timedelta(days=INSTAGRAM_MEDIA_WINDOW_DAYS)
        media_resp = self._request(
            "GET",
            f"{API_BASE}/me/media",
            access_token=access_token,
            params={
                "fields": "id,timestamp,permalink,caption,media_type,media_url,thumbnail_url",
                "limit": INSTAGRAM_MEDIA_SCAN_LIMIT,
                "since": int(media_floor.timestamp()),
            },
        )
        media_items = media_resp.json().get("data", [])

        cutoff = None
        if since:
            aware_since = since if since.tzinfo else since.replace(tzinfo=UTC)
            cutoff = aware_since - timedelta(hours=INSTAGRAM_COMMENT_LOOKBACK_HOURS)

        messages: list[InboxMessage] = []
        seen: set[str] = set()

        for media in media_items:
            media_id = str(media.get("id") or "")
            if not media_id:
                continue

            # For image posts, the media URL is the actual photo and is the most
            # reliable preview source. Instagram can sometimes return a
            # thumbnail_url-shaped value even when the post is not a video;
            # normalizing it here prevents the inbox card from showing the wrong
            # asset. Videos keep their dedicated thumbnail when available.
            media = dict(media)
            media_type = str(media.get("media_type") or "").upper()
            if media_type != "VIDEO" and media.get("media_url"):
                media["thumbnail_url"] = media["media_url"]

            comments_payload = None
            last_error: Exception | None = None
            for fields in INSTAGRAM_COMMENT_FIELD_SETS:
                try:
                    resp = self._request(
                        "GET",
                        f"{API_BASE}/{media_id}/comments",
                        access_token=access_token,
                        params={"fields": fields, "limit": INSTAGRAM_COMMENTS_PER_MEDIA},
                    )
                except Exception as exc:
                    last_error = exc
                    if getattr(exc, "status_code", None) == 400:
                        logger.info(
                            "Instagram Login rejected comment fields for media %s; retrying with fewer fields",
                            media_id,
                        )
                        continue
                    logger.warning("Skipping Instagram comments for media %s: %s", media_id, exc)
                    break
                else:
                    comments_payload = resp.json()
                    break

            if comments_payload is None:
                if last_error is not None:
                    logger.warning("No readable Instagram comments for media %s: %s", media_id, last_error)
                continue

            try:
                for comment in _iter_comments(self._request, API_BASE, access_token, comments_payload):
                    messages.extend(
                        _comment_to_messages(
                            comment,
                            media,
                            owner_id,
                            owner_handle,
                            cutoff,
                            seen,
                        )
                    )
            except Exception as exc:
                logger.warning("Instagram Login comment pagination failed for media %s: %s", media_id, exc)

        logger.info("Instagram Login explicit comment poll produced %d message(s)", len(messages))
        return messages
