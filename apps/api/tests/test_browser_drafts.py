"""Browser-extension capture endpoint integration tests."""

from __future__ import annotations

import json

import pytest
from django.test import Client
from django.utils import timezone

from apps.api_keys import services
from apps.common.models import ContentPerformanceProfile, UGCSubmission
from apps.composer.models import PlatformPost, Post
from apps.members.models import PERMISSION_KEYS, OrgMembership, WorkspaceMembership


class _SecureClient(Client):
    def generic(self, method, path, *args, **kwargs):
        kwargs["secure"] = True
        return super().generic(method, path, *args, **kwargs)


@pytest.fixture
def user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="browser-extension@example.com",
        password="testpass123",
        name="Browser Extension",
        tos_accepted_at=timezone.now(),
    )


@pytest.fixture
def organization(db):
    from apps.organizations.models import Organization

    return Organization.objects.create(name="Browser Extension Org")


@pytest.fixture
def workspace(db, organization):
    from apps.workspaces.models import Workspace

    return Workspace.objects.create(name="Browser Extension WS", organization=organization)


@pytest.fixture
def owner_memberships(db, user, organization, workspace):
    OrgMembership.objects.create(
        user=user,
        organization=organization,
        org_role=OrgMembership.OrgRole.OWNER,
    )
    return WorkspaceMembership.objects.create(
        user=user,
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.OWNER,
    )


@pytest.fixture
def social_account(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="facebook",
        account_platform_id="fb-browser-extension",
        account_name="TN Game",
        connection_status="connected",
    )


@pytest.fixture
def other_account(db, workspace):
    from apps.social_accounts.models import SocialAccount

    return SocialAccount.objects.create(
        workspace=workspace,
        platform="instagram",
        account_platform_id="ig-not-allowed",
        account_name="TN Waterfalls",
        connection_status="connected",
    )


@pytest.fixture
def issued_key(db, user, owner_memberships, workspace, social_account):
    return services.issue_api_key(
        workspace=workspace,
        social_accounts=[social_account],
        issued_by=user,
        name="browser extension",
        permissions=list(PERMISSION_KEYS),
    )


@pytest.fixture
def client_with_token(issued_key):
    return _SecureClient(HTTP_AUTHORIZATION=f"Bearer {issued_key.plaintext_token}")


def _payload(social_account, **overrides):
    payload = {
        "social_account_id": str(social_account.id),
        "source_url": "https://www.instagram.com/p/Tennessee123/?utm_source=copy_link#comments",
        "source_platform": "instagram",
        "source_external_id": "Tennessee123",
        "creator_handle": "tn.creator",
        "creator_name": "Tennessee Creator",
        "title": "Greeter Falls in spring",
        "caption": "A quiet morning at Greeter Falls.",
        "media_asset_ids": [],
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
class TestCreateBrowserDraft:
    def test_creates_rights_aware_editable_draft(self, client_with_token, social_account):
        response = client_with_token.post(
            "/api/v1/browser-drafts/",
            data=json.dumps(_payload(social_account)),
            content_type="application/json",
        )

        assert response.status_code == 201, response.content
        body = response.json()
        assert body["status"] == "draft"
        assert body["rights_status"] == "not_requested"
        assert body["duplicate"] is False

        post = Post.objects.get(id=body["post_id"])
        submission = UGCSubmission.objects.get(id=body["submission_id"])
        profile = ContentPerformanceProfile.objects.get(post=post)
        platform_post = PlatformPost.objects.get(post=post)

        assert platform_post.social_account == social_account
        assert platform_post.status == PlatformPost.Status.DRAFT
        assert post.caption == "A quiet morning at Greeter Falls."
        assert "instagram.com/p/Tennessee123" in post.internal_notes
        assert submission.status == UGCSubmission.Status.PENDING
        assert submission.contributor_handle == "tn.creator"
        assert submission.target_url.startswith("https://www.instagram.com/p/Tennessee123/")
        assert submission.metadata["provenance"]["platform"] == "instagram"
        assert submission.metadata["provenance"]["source_url"].endswith("?utm_source=copy_link")
        assert submission.rights_passport.status == "not_requested"
        assert profile.source_submission == submission
        assert profile.source_type == ContentPerformanceProfile.SourceType.UGC
        assert str(post.id) in body["edit_path"]

        from apps.composer.ugc_publish_guard import post_publish_preflight

        preflight = post_publish_preflight(post.workspace, post)
        assert preflight["is_ugc"] is True
        assert preflight["is_safe"] is False
        assert preflight["blockers"][0]["code"] == "rights_blocked"

    def test_same_source_and_account_returns_existing_draft(self, client_with_token, social_account):
        first = client_with_token.post(
            "/api/v1/browser-drafts/",
            data=json.dumps(_payload(social_account)),
            content_type="application/json",
        )
        second = client_with_token.post(
            "/api/v1/browser-drafts/",
            data=json.dumps(_payload(social_account, caption="A later DOM rendering.")),
            content_type="application/json",
        )

        assert first.status_code == 201, first.content
        assert second.status_code == 200, second.content
        assert second.json()["duplicate"] is True
        assert second.json()["post_id"] == first.json()["post_id"]
        assert Post.objects.count() == 1
        assert UGCSubmission.objects.count() == 1

    def test_rejects_account_outside_key_allowlist(self, client_with_token, other_account):
        response = client_with_token.post(
            "/api/v1/browser-drafts/",
            data=json.dumps(_payload(other_account)),
            content_type="application/json",
        )

        assert response.status_code == 403
        assert Post.objects.count() == 0

    @pytest.mark.parametrize(
        "source_url",
        [
            "file:///Users/dakota/post.html",
            "https://name:secret@example.com/post",
            "not a URL",
        ],
    )
    def test_rejects_non_public_source_urls(self, client_with_token, social_account, source_url):
        response = client_with_token.post(
            "/api/v1/browser-drafts/",
            data=json.dumps(_payload(social_account, source_url=source_url)),
            content_type="application/json",
        )

        assert response.status_code == 422
        assert Post.objects.count() == 0
