"""TN Social Studio compatibility fixes for Instagram Login publishing.

Keep TN-specific production fixes isolated from the upstream provider so the
core BrightBean provider remains easy to compare/update.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from .instagram_login import InstagramLoginProvider
from .types import PostType, PublishContent, PublishResult

logger = logging.getLogger(__name__)


class TNInstagramLoginProvider(InstagramLoginProvider):
    """Instagram Login provider with TN Social Studio publishing safeguards."""

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
