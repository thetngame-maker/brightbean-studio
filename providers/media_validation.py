"""Local media checks shared by the composer and publishing providers."""

from urllib.parse import urlsplit

from .exceptions import PublishError

MEDIA_ERRORS = {
    "instagram_limit": "Instagram publishing supports at most 10 photos or videos per carousel. Choose up to 10 items or split this into separate posts.",
    "instagram_story": "Instagram Stories require one photo or video per post. Choose one item or use a feed carousel.",
    "facebook_video": "Facebook multi-photo posts support images only; post videos separately. Choose photos for this Facebook post, or publish one video at a time.",
}


class MediaValidationError(PublishError):
    def __init__(self, code, platform):
        self.code = code
        super().__init__(MEDIA_ERRORS[code], platform=platform, retryable=False)


def media_kind(url):
    return "video" if urlsplit(url).path.lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".avi")) else "image"


def validate_media(platform, kinds, post_type=None):
    if platform in ("instagram", "instagram_login"):
        if len(kinds) > 10:
            raise MediaValidationError("instagram_limit", platform)
        if len(kinds) > 1 and post_type == "story":
            raise MediaValidationError("instagram_story", platform)
    if platform == "facebook" and len(kinds) > 1 and "video" in kinds:
        raise MediaValidationError("facebook_video", platform)
