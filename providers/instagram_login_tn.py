"""TN Social Studio compatibility fixes for Instagram Login publishing.

Keep TN-specific production fixes isolated from the upstream provider so the
core BrightBean provider remains easy to compare/update.
"""

from __future__ import annotations

from .instagram_login import InstagramLoginProvider
from .types import PostType, PublishContent, PublishResult


class TNInstagramLoginProvider(InstagramLoginProvider):
    """Instagram Login provider with TN Social Studio publishing safeguards."""

    def _publish_single(self, access_token: str, content: PublishContent) -> PublishResult:
        # The publisher engine intentionally resolves a lone video asset to
        # PostType.VIDEO when there is no explicit per-platform post_type hint.
        # Instagram no longer publishes standalone feed videos; they must use
        # the REELS container flow. The legacy Facebook-login Instagram provider
        # already handles VIDEO this way, but the direct Instagram Login provider
        # only checked REEL and therefore sent the video URL as image_url.
        if content.post_type != PostType.VIDEO:
            return super()._publish_single(access_token, content)

        payload: dict = {
            "media_type": "REELS",
            "video_url": content.media_urls[0],
        }
        if content.text:
            payload["caption"] = content.text

        container_id = self._create_container(access_token, payload)
        self._wait_for_container(access_token, container_id)
        return self._publish_container(access_token, container_id)
