"""Integration tests for the iPhone Share Sheet capture handoff."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.utils import timezone

from apps.common.models import ContentPerformanceProfile, UGCSubmission
from apps.composer.models import PlatformPost, Post, PostMedia
from apps.media_library.models import MediaAsset
from apps.members.models import OrgMembership, WorkspaceMembership
from apps.social_accounts.models import SocialAccount


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="iphone-shortcut@example.com",
        password="testpass123",
        name="iPhone Shortcut",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="iPhone Capture Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="iPhone Capture WS", organization=organization)


@pytest.fixture
def membership(db, user, organization, workspace):
    OrgMembership.objects.create(user=user, organization=organization, org_role=OrgMembership.OrgRole.OWNER)
    return WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )


@pytest.fixture
def social_account(db, workspace):
    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-iphone-shortcut",
        account_name="Tennessee Waterfalls",
        connection_status=SocialAccount.ConnectionStatus.CONNECTED,
    )


@pytest.fixture
def signed_in_client(user, membership):
    client = Client()
    client.force_login(user)
    return client


def _payload(social_account, **overrides):
    payload = {
        "social_account_id": str(social_account.id),
        "source_url": "https://www.instagram.com/p/Shortcut123/?share_id=ios#comments",
        "creator_handle": "tn.creator",
        "creator_name": "Tennessee Creator",
        "title": "Greeter Falls from iPhone",
        "caption": "A quiet morning at Greeter Falls.",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestMobileCapture:
    def test_requires_sign_in_and_preserves_shared_source(self):
        response = Client().get(
            "/mobile-capture/",
            {"source": "https://www.instagram.com/p/LoginReturn/"},
        )

        assert response.status_code == 302
        assert "/accounts/login/" in response.url
        assert "next=" in response.url

    def test_prefills_source_and_resolves_first_workspace(
        self, signed_in_client, user, workspace, social_account
    ):
        response = signed_in_client.get(
            "/mobile-capture/",
            {"source": "https://www.facebook.com/groups/123/posts/456/"},
        )

        assert response.status_code == 200
        assert response.context["workspace"] == workspace
        assert response.context["form"]["source_url"] == "https://www.facebook.com/groups/123/posts/456/"
        assert str(social_account.id) in response.content.decode()
        user.refresh_from_db()
        assert user.last_workspace_id == workspace.id

    def test_creates_rights_aware_editable_draft(
        self, signed_in_client, workspace, social_account
    ):
        response = signed_in_client.post("/mobile-capture/", _payload(social_account))

        assert response.status_code == 302
        assert "capture=ios" in response.url
        post = Post.objects.get()
        submission = UGCSubmission.objects.get()
        profile = ContentPerformanceProfile.objects.get(post=post)
        platform_post = PlatformPost.objects.get(post=post)

        assert platform_post.social_account == social_account
        assert platform_post.status == PlatformPost.Status.DRAFT
        assert post.caption == "A quiet morning at Greeter Falls."
        assert "iPhone Shortcut" in post.internal_notes
        assert submission.workspace == workspace
        assert submission.contributor_handle == "tn.creator"
        assert submission.metadata["provenance"]["platform"] == "instagram"
        assert submission.metadata["provenance"]["discovery_source"] == "ios_shortcut"
        assert submission.metadata["ios_shortcut_capture"]["media_count"] == 0
        assert submission.rights_passport.status == "not_requested"
        assert profile.source_submission == submission

        from apps.composer.ugc_publish_guard import post_publish_preflight

        preflight = post_publish_preflight(workspace, post)
        assert preflight["is_ugc"] is True
        assert preflight["is_safe"] is False

    def test_same_source_and_account_opens_existing_draft(
        self, signed_in_client, social_account
    ):
        first = signed_in_client.post("/mobile-capture/", _payload(social_account))
        second = signed_in_client.post(
            "/mobile-capture/",
            _payload(social_account, caption="A second capture."),
        )

        assert first.status_code == 302
        assert second.status_code == 302
        assert str(Post.objects.get().id) in second.url
        assert Post.objects.count() == 1
        assert UGCSubmission.objects.count() == 1

    def test_attaches_camera_roll_media(self, signed_in_client, social_account):
        png = SimpleUploadedFile(
            "waterfall.png",
            b"\x89PNG\r\n\x1a\n" + (b"\x00" * 64),
            content_type="image/png",
        )
        payload = _payload(social_account)
        payload["media"] = png

        response = signed_in_client.post("/mobile-capture/", payload)

        assert response.status_code == 302
        asset = MediaAsset.objects.get()
        attachment = PostMedia.objects.get()
        submission = UGCSubmission.objects.get()
        assert asset.filename == "waterfall.png"
        assert asset.media_type == MediaAsset.MediaType.IMAGE
        assert attachment.media_asset == asset
        assert submission.media_asset == asset
        assert submission.metadata["ios_shortcut_capture"]["media_count"] == 1

    @pytest.mark.parametrize(
        "source_url",
        ["file:///private/var/mobile/post", "https://name:secret@example.com/post", "not a URL"],
    )
    def test_rejects_invalid_source_urls(
        self, signed_in_client, social_account, source_url
    ):
        response = signed_in_client.post(
            "/mobile-capture/",
            _payload(social_account, source_url=source_url),
        )

        assert response.status_code == 400
        assert b"valid public web address" in response.content or b"must not contain credentials" in response.content
        assert Post.objects.count() == 0

    def test_rejects_account_from_another_workspace(
        self, signed_in_client, social_account, organization
    ):
        from apps.workspaces.models import Workspace

        other_workspace = Workspace.objects.create(name="Other", organization=organization)
        other_account = SocialAccount.objects.create(
            workspace=other_workspace,
            platform="facebook",
            account_platform_id="other-fb",
            account_name="Other Facebook",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

        response = signed_in_client.post("/mobile-capture/", _payload(other_account))

        assert response.status_code == 400
        assert b"Choose a connected Studio account" in response.content
        assert Post.objects.count() == 0

    def test_requires_create_posts_permission(
        self, signed_in_client, membership, social_account
    ):
        membership.workspace_role = WorkspaceMembership.WorkspaceRole.VIEWER
        membership.save(update_fields=["workspace_role"])

        response = signed_in_client.get("/mobile-capture/")

        assert response.status_code == 403
