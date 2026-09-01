import json
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import AuditEvent, ContentPerformanceProfile, UGCRightsPassport, UGCSubmission
from apps.social_accounts.models import SocialAccount

from ..caption_improvement import generate_improved_caption
from ..models import PlatformPost, Post


def responses_api_result(caption):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"output_text": json.dumps({"caption": caption})}
    return response


class CaptionImprovementTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="caption-ai@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-caption-ai",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Northrup Falls",
            caption="Northrup Falls\n\n#film #waterfalls",
        )
        PlatformPost.objects.create(post=self.post, social_account=self.account)
        self.client.force_login(self.user)
        self.url = reverse("composer:improve_caption", kwargs={"workspace_id": self.workspace.id})

    @patch("apps.composer.caption_improvement.httpx.post")
    def test_service_uses_structured_response_and_preserves_required_credit(self, post_mock):
        post_mock.return_value = responses_api_result("A quiet Tennessee waterfall worth saving for later.")

        result = generate_improved_caption(
            source_caption="Northrup Falls. Photo: @blakenyon",
            title="Northrup Falls",
            account_labels=["Instagram: TN Game Instagram"],
            target_length=500,
            required_credit="@blakenyon",
            api_key="test-key",
        )

        self.assertEqual(result, "A quiet Tennessee waterfall worth saving for later.\n\n@blakenyon")
        request_payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(request_payload["model"], "gpt-5-mini")
        self.assertEqual(request_payload["text"]["format"]["type"], "json_schema")
        prompt_data = json.loads(request_payload["input"])
        self.assertEqual(prompt_data["previous_caption"], "Northrup Falls. Photo: @blakenyon")
        self.assertEqual(prompt_data["maximum_characters"], 500)
        self.assertEqual(prompt_data["protected_creator_credit"], "@blakenyon")

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_CAPTION_MODEL="gpt-5-mini")
    @patch("apps.composer.caption_improvement.httpx.post")
    def test_endpoint_returns_comparison_without_changing_saved_post(self, post_mock):
        post_mock.return_value = responses_api_result(
            "Would you add Northrup Falls to your Tennessee list?\n\n#TennesseeWaterfalls"
        )
        previous = "Northrup Falls\n\n#film #waterfalls"

        response = self.client.post(
            self.url,
            {
                "caption": previous,
                "title": self.post.title,
                "selected_accounts": str(self.account.id),
                "post_id": str(self.post.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["previous_caption"], previous)
        self.assertIn("Would you add Northrup Falls", response.json()["suggested_caption"])
        self.post.refresh_from_db()
        self.assertEqual(self.post.caption, previous)
        event = AuditEvent.objects.get(action="composer.caption_ai_generated")
        self.assertEqual(event.target_id, str(self.post.id))
        self.assertNotIn(previous, json.dumps(event.metadata))

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("apps.composer.caption_improvement.httpx.post")
    def test_endpoint_restores_canonical_ugc_credit(self, post_mock):
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            contributor_handle="blakenyon",
            title="Northrup Falls",
            consent_confirmed=True,
            consent_version="creator-rights-portal-v1",
            consent_at=timezone.now(),
            metadata={"permission": {"status": "granted"}},
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.credit_required = True
        passport.credit_text = "@blakenyon"
        passport.save(
            update_fields=[
                "status",
                "allow_organic_social",
                "credit_required",
                "credit_text",
                "updated_at",
            ]
        )
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=self.post,
            source_submission=submission,
            source_type=ContentPerformanceProfile.SourceType.UGC,
            created_by=self.user,
        )
        post_mock.return_value = responses_api_result("A better waterfall caption without the credit line.")

        response = self.client.post(
            self.url,
            {
                "caption": self.post.caption,
                "selected_accounts": str(self.account.id),
                "post_id": str(self.post.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["suggested_caption"].endswith("@blakenyon"))

    @override_settings(OPENAI_API_KEY="")
    def test_missing_ai_configuration_leaves_caption_unchanged(self):
        response = self.client.post(
            self.url,
            {"caption": self.post.caption, "post_id": str(self.post.id)},
        )

        self.assertEqual(response.status_code, 503)
        self.assertIn("not configured", response.json()["error"])
        self.post.refresh_from_db()
        self.assertEqual(self.post.caption, "Northrup Falls\n\n#film #waterfalls")

    @override_settings(OPENAI_API_KEY="test-key")
    @patch("apps.composer.caption_improvement.httpx.post")
    def test_ai_failure_is_safe_and_does_not_create_an_audit_event(self, post_mock):
        post_mock.side_effect = ValueError("bad payload")

        response = self.client.post(
            self.url,
            {"caption": self.post.caption, "post_id": str(self.post.id)},
        )

        self.assertEqual(response.status_code, 502)
        self.assertIn("unchanged", response.json()["error"])
        self.assertFalse(AuditEvent.objects.filter(action="composer.caption_ai_generated").exists())

    def test_composer_renders_ai_control_for_existing_and_new_posts(self):
        existing = self.client.get(
            reverse(
                "composer:compose_edit",
                kwargs={"workspace_id": self.workspace.id, "post_id": self.post.id},
            )
        )
        new = self.client.get(reverse("composer:compose", kwargs={"workspace_id": self.workspace.id}))

        self.assertContains(existing, "Improve with AI")
        self.assertContains(existing, str(self.url))
        self.assertContains(new, "Improve with AI")
