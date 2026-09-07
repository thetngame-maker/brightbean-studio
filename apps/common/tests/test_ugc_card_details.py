import io
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from apps.composer.models import PlatformPost, Post, PostMedia
from apps.composer.tests.test_unsplash import ComposerTestCase
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount

from ..models import UGCSubmission
from ..ugc_card_details import decorate_cards


class CommunityCardTests(ComposerTestCase):
    def setUp(self):
        super().setUp()
        self.item = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind="photo",
            status="approved",
            title="Falls",
            target_type="top_sight",
            target_id="falls",
            metadata={"discovery_import": {"like_count": 0, "comment_count": 23}},
        )
        self.url = reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id})

    def test_counts_and_account_statuses_render_without_conflating_draft_and_published(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, title="Falls post")
        fb = SocialAccount.objects.create(
            workspace=self.workspace, platform="facebook", account_name="TN Game", account_platform_id="fb"
        )
        ig = SocialAccount.objects.create(
            workspace=self.workspace, platform="instagram_login", account_name="TN Waterfalls", account_platform_id="ig"
        )
        PlatformPost.objects.create(post=post, social_account=fb, status="draft")
        PlatformPost.objects.create(post=post, social_account=ig, status="published")
        self.item.metadata["studio_post_ids"] = [str(post.id), str(post.id), "invalid"]
        self.item.save()
        response = self.client.get(self.url, {"tab": "approved"})
        self.assertEqual(response.status_code, 200)
        for text in ["TN Game", "TN Waterfalls", "Draft", "Published", "23", "Original post", "Studio usage · 1 post"]:
            self.assertContains(response, text)
        self.assertContains(response, "<strong>0</strong> likes", html=False)

    def test_cross_workspace_and_deleted_post_ids_are_not_counted(self):
        from apps.workspaces.models import Workspace

        other = Workspace.objects.create(organization=self.org, name="Other")
        post = Post.objects.create(workspace=other, author=self.user)
        self.item.metadata["studio_post_ids"] = [str(post.id), "00000000-0000-0000-0000-000000000000"]
        decorate_cards([self.item], self.workspace)
        self.assertEqual(self.item.studio_usage_count, 0)

    def test_reuse_through_shared_media_is_counted(self):
        asset = MediaAsset.objects.create(
            workspace=self.workspace, organization=self.org, filename="a.jpg", media_type="image", file_size=1
        )
        self.item.media_asset = asset
        post = Post.objects.create(workspace=self.workspace, author=self.user)
        PostMedia.objects.create(post=post, media_asset=asset)
        decorate_cards([self.item], self.workspace)
        self.assertEqual(self.item.studio_usage_count, 1)

    def test_engagement_sort_selects_from_all_submissions(self):
        UGCSubmission.objects.bulk_create(
            [
                UGCSubmission(
                    workspace=self.workspace,
                    kind="photo",
                    status="approved",
                    target_type="top_sight",
                    target_id=str(i),
                    title=f"Other {i}",
                )
                for i in range(101)
            ]
        )
        response = self.client.get(self.url, {"tab": "approved", "sort": "engaged"})
        self.assertEqual(response.context["submissions"][0].id, self.item.id)

    @override_settings(MEDIA_ROOT="/private/tmp/ugc-card-test-media")
    def test_preview_serves_stored_image_and_does_not_require_external_cdn(self):
        buffer = io.BytesIO()
        Image.new("RGB", (8, 8), "blue").save(buffer, format="JPEG")
        asset = MediaAsset.objects.create(
            workspace=self.workspace,
            organization=self.org,
            filename="photo.jpg",
            media_type="image",
            mime_type="image/jpeg",
            file_size=len(buffer.getvalue()),
        )
        asset.file.save("test-preview.jpg", ContentFile(buffer.getvalue()))
        self.item.media_asset = asset
        self.item.save()
        url = reverse("ugc:card_preview", kwargs={"workspace_id": self.workspace.id, "submission_id": self.item.id})
        with patch("apps.common.ugc_card_previews.recover_card_preview") as repair:
            response = self.client.get(url)
            self.assertEqual(response.json()["status"], "ready")
            response = self.client.get(url, {"image": 1})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b"".join(response.streaming_content), buffer.getvalue())
            repair.assert_not_called()
        asset.file.delete()

    def test_missing_preview_queues_only_once_and_is_workspace_scoped(self):
        url = reverse("ugc:card_preview", kwargs={"workspace_id": self.workspace.id, "submission_id": self.item.id})
        with patch("apps.common.ugc_card_previews.recover_card_preview") as repair:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.client.post(url).json()["status"], "loading")
                self.assertEqual(self.client.post(url).json()["status"], "loading")
            repair.assert_called_once_with(str(self.item.id), priority=100)
        from apps.workspaces.models import Workspace

        other = Workspace.objects.create(organization=self.org, name="Other")
        self.item.workspace = other
        self.item.save()
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_failed_recovery_records_unavailable(self):
        from ..ugc_card_previews import recover_card_preview

        with patch(
            "apps.common.ugc_approved_media_repair.repair_one_approved_submission", side_effect=ValueError("expired")
        ):
            recover_card_preview.now(str(self.item.id))
        self.item.refresh_from_db()
        self.assertEqual(self.item.metadata["card_preview_repair"]["status"], "unavailable")

    def test_recently_refreshed_media_can_preview_when_server_download_is_unavailable(self):
        from django.utils import timezone

        self.item.metadata["discovery_import"].update(
            {
                "media_refreshed_at": timezone.now().isoformat(),
                "media_url": "https://example.com/photo.jpg",
                "thumbnail_url": "https://example.com/thumb.jpg",
                "media_type": "image",
            }
        )
        self.item.save()
        url = reverse("ugc:card_preview", kwargs={"workspace_id": self.workspace.id, "submission_id": self.item.id})
        self.assertEqual(self.client.get(url).json()["thumbnail"], "https://example.com/thumb.jpg")

    def test_preview_recovery_does_not_download_entire_carousel(self):
        from ..ugc_approved_media_repair import repair_one_approved_submission

        self.item.metadata["provenance"] = {"platform": "instagram", "source_url": "https://www.instagram.com/p/test/"}
        self.item.save()
        refreshed = {"media_items": [{"media_type": "image", "media_url": "https://example.com/a.jpg"}]}
        with (
            patch("apps.common.ugc_approved_media_repair.fetch_instagram_post_details", return_value=refreshed),
            patch(
                "apps.common.ugc_approved_media_repair.capture_submission_media", return_value=(False, "unavailable")
            ) as single,
            patch("apps.common.ugc_approved_media_repair.capture_submission_gallery") as gallery,
        ):
            repair_one_approved_submission(self.item, preview_only=True)
        single.assert_called_once()
        gallery.assert_not_called()
        self.item.refresh_from_db()
        self.assertIn("media_refreshed_at", self.item.metadata["discovery_import"])
