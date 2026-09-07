from django.urls import reverse

from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount

from .test_unsplash import ComposerTestCase


class MediaPreflightTests(ComposerTestCase):
    def setup_post(self, platform, kinds):
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="Photos")
        self.account = SocialAccount.objects.create(
            workspace=self.workspace, platform=platform, account_platform_id="test", account_name="Test account"
        )
        self.pp = PlatformPost.objects.create(post=self.post, social_account=self.account, status="draft")
        for index, kind in enumerate(kinds):
            asset = MediaAsset.objects.create(
                workspace=self.workspace,
                organization=self.org,
                filename=f"{index}.{'mp4' if kind == 'video' else 'jpg'}",
                media_type=kind,
                file_size=1,
            )
            PostMedia.objects.create(post=self.post, media_asset=asset, position=index)
        self.url = reverse(
            "composer:save_post_edit", kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id}
        )

    def submit(self, action):
        return self.client.post(
            self.url, {"action": action, "caption": "Photos", "selected_accounts": str(self.account.id)}
        )

    def test_oversized_instagram_post_is_not_queued(self):
        self.setup_post("instagram_login", ["image"] * 13)
        response = self.submit("publish_now")
        self.assertEqual(response.status_code, 400)
        self.assertIn("10", response.json()["errors"]["media"][0])
        self.pp.refresh_from_db()
        self.assertEqual(self.pp.status, "draft")

    def test_facebook_mixed_media_is_not_queued(self):
        self.setup_post("facebook", ["image", "video"])
        response = self.submit("add_to_queue")
        self.assertEqual(response.status_code, 400)
        self.assertIn("photos", response.json()["errors"]["media"][0])
        self.pp.refresh_from_db()
        self.assertEqual(self.pp.status, "draft")

    def test_invalid_publishing_selection_can_still_be_saved_as_draft(self):
        self.setup_post("instagram_login", ["image"] * 13)
        response = self.submit("save_draft")
        self.assertIn(response.status_code, (204, 302))
        self.assertEqual(self.post.media_attachments.count(), 13)
