from unittest.mock import MagicMock

import pytest

from apps.publisher.engine import PublishEngine
from apps.social_accounts.error_messages import friendly_publish_error
from providers.facebook import FacebookProvider
from providers.instagram import InstagramProvider
from providers.instagram_login import InstagramLoginProvider
from providers.instagram_login_tn import TNInstagramLoginProvider
from providers.media_validation import MediaValidationError, validate_media
from providers.types import PostType, PublishContent


@pytest.mark.parametrize("platform", ["instagram", "instagram_login"])
@pytest.mark.parametrize("hint", ["image", "video", "reel", "carousel", None])
def test_multiple_items_override_stale_single_item_hint(platform, hint):
    assert PublishEngine._resolve_post_type(platform, {"post_type": hint}, 3, "image") == PostType.CAROUSEL


@pytest.mark.parametrize("provider_class", [InstagramProvider, InstagramLoginProvider, TNInstagramLoginProvider])
def test_provider_does_not_silently_publish_first_item(provider_class):
    provider = provider_class({})
    provider._publish_carousel = MagicMock()
    provider._publish_single = MagicMock()
    content = PublishContent(
        media_urls=["https://example.com/a.jpg", "https://example.com/b.jpg"],
        post_type=PostType.IMAGE,
        extra={"ig_user_id": "ig"},
    )
    provider.publish_post("token", content)
    provider._publish_carousel.assert_called_once()
    provider._publish_single.assert_not_called()


@pytest.mark.parametrize("provider_class", [InstagramProvider, InstagramLoginProvider, TNInstagramLoginProvider])
def test_oversized_carousel_fails_before_upload_and_without_retry(provider_class):
    provider = provider_class({})
    provider._request = MagicMock()
    with pytest.raises(MediaValidationError) as error:
        provider.publish_post(
            "token",
            PublishContent(media_urls=[f"https://example.com/{i}.jpg" for i in range(13)], post_type=PostType.IMAGE),
        )
    assert error.value.retryable is False
    assert "10" in friendly_publish_error(error.value)
    provider._request.assert_not_called()


@pytest.mark.parametrize("provider_class", [InstagramLoginProvider, TNInstagramLoginProvider])
def test_signed_video_url_is_staged_as_video_in_carousel(provider_class):
    provider = provider_class({})
    provider._create_container = MagicMock(side_effect=["photo", "video", "parent"])
    provider._wait_for_container = MagicMock()
    provider._publish_container = MagicMock()
    provider.publish_post(
        "token",
        PublishContent(
            media_urls=["https://example.com/a.jpg", "https://example.com/b.MP4?signature=test"],
            post_type=PostType.IMAGE,
        ),
    )
    payload = provider._create_container.call_args_list[1].args[1]
    assert payload["media_type"] == "VIDEO"
    assert "video_url" in payload
    assert provider._create_container.call_args_list[2].args[1]["children"] == "photo,video"


def test_facebook_mixed_media_is_permanent_failure_even_when_video_is_first():
    provider = FacebookProvider({})
    provider._request = MagicMock()
    with pytest.raises(MediaValidationError) as error:
        provider.publish_post(
            "token",
            PublishContent(
                media_urls=["https://example.com/a.mp4?sig=x", "https://example.com/b.jpg"],
                post_type=PostType.VIDEO,
                extra={"page_id": "page"},
            ),
        )
    assert error.value.retryable is False
    assert "post videos separately" in friendly_publish_error(error.value)
    provider._request.assert_not_called()


def test_valid_photo_collection_and_ten_item_carousel_are_allowed():
    validate_media("facebook", ["image"] * 9)
    validate_media("instagram_login", ["image", "video"] * 5)


def test_multi_item_story_is_rejected_instead_of_truncated():
    with pytest.raises(MediaValidationError):
        validate_media("instagram_login", ["image"] * 2, "story")
