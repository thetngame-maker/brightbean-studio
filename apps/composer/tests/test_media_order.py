from django.urls import reverse

from apps.composer.models import Post, PostMedia
from apps.media_library.models import MediaAsset

from .test_unsplash import ComposerTestCase


class MediaOrderTests(ComposerTestCase):
    def setUp(self):
        super().setUp()
        self.post = Post.objects.create(workspace=self.workspace, author=self.user, caption="Photos")
        self.assets = [
            MediaAsset.objects.create(
                workspace=self.workspace,
                organization=self.org,
                filename=f"{i}.jpg",
                media_type="image",
                file_size=1,
            )
            for i in range(3)
        ]
        for i, asset in enumerate(self.assets):
            PostMedia.objects.create(post=self.post, media_asset=asset, position=i)
        self.url = reverse("composer:reorder_media", kwargs={"workspace_id": self.workspace.id})
        self.order = [str(asset.id) for asset in reversed(self.assets)]

    def test_order_persists_on_post(self):
        response = self.client.post(self.url, {"post_id": str(self.post.id), "media_order": self.order})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([str(item.media_asset_id) for item in self.post.media_attachments.all()], self.order)

    def test_stale_and_duplicate_orders_do_not_mutate_post(self):
        for order in [self.order[:2], [self.order[0]] * 3, self.order + ["foreign"]]:
            response = self.client.post(self.url, {"post_id": str(self.post.id), "media_order": order})
            self.assertEqual(response.status_code, 409)
        self.assertEqual([item.position for item in self.post.media_attachments.all()], [0, 1, 2])

    def test_pending_order_persists_in_session(self):
        session = self.client.session
        key = f"pending_media_{self.workspace.id}"
        session[key] = list(reversed(self.order))
        session.save()
        response = self.client.post(self.url, {"media_order": self.order})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session[key], self.order)

    def test_post_outside_workspace_is_not_reordered(self):
        from apps.workspaces.models import Workspace

        other = Workspace.objects.create(organization=self.org, name="Other")
        self.post.workspace = other
        self.post.save()
        response = self.client.post(self.url, {"post_id": str(self.post.id), "media_order": self.order})
        self.assertEqual(response.status_code, 404)

    def test_invalid_post_id_is_rejected(self):
        response = self.client.post(self.url, {"post_id": "invalid", "media_order": self.order})
        self.assertEqual(response.status_code, 400)

    def test_reopened_editor_uses_saved_order_and_full_media_preview_urls(self):
        self.client.post(self.url, {"post_id": str(self.post.id), "media_order": self.order})
        response = self.client.get(
            reverse(
                "composer:compose_edit",
                kwargs={
                    "workspace_id": self.workspace.id,
                    "post_id": self.post.id,
                },
            )
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        indexes = [html.index(f'data-media-id="{asset_id}"') for asset_id in self.order]
        self.assertEqual(indexes, sorted(indexes))
        for asset in self.assets:
            url = reverse(
                "composer:media_stream",
                kwargs={
                    "workspace_id": self.workspace.id,
                    "asset_id": asset.id,
                },
            )
            self.assertIn(f'data-preview-url="{url}"', html)
        self.assertIn("js/composer-media.js", html)
