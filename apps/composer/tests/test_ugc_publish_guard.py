from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import AuditEvent, ContentPerformanceProfile, UGCRightsPassport, UGCSubmission
from apps.publisher.engine import PublishEngine
from apps.social_accounts.models import SocialAccount

from ..models import PlatformPost, Post
from ..orchestration import build_orchestration
from ..ugc_publish_guard import caption_has_required_credit, post_publish_preflight


class UGCPublishGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="credit-guard@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram",
            account_platform_id="ig-credit-guard",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def create_guarded_post(self, *, caption="Visit Machine Falls.\n\nCommunity content by @tn_hiker"):
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            contributor_handle="tn_hiker",
            title="Machine Falls",
            consent_confirmed=True,
            consent_version="creator-rights-portal-v1",
            consent_at=timezone.now(),
            metadata={"permission": {"status": "granted"}},
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.credit_required = True
        passport.credit_text = "@tn_hiker"
        passport.save(
            update_fields=[
                "status",
                "allow_organic_social",
                "credit_required",
                "credit_text",
                "updated_at",
            ]
        )
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Machine Falls",
            caption=caption,
        )
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=post,
            source_submission=submission,
            source_type=ContentPerformanceProfile.SourceType.UGC,
            created_by=self.user,
        )
        variant = PlatformPost.objects.create(
            post=post,
            social_account=self.account,
            status=PlatformPost.Status.DRAFT,
        )
        return submission, post, variant

    def test_credit_matching_is_case_insensitive_but_requires_intact_saved_text(self):
        self.assertTrue(caption_has_required_credit("Photo: @TN_HIKER", "@tn_hiker"))
        self.assertTrue(caption_has_required_credit("Photo by Dakota   Meeks", "Dakota Meeks"))
        self.assertFalse(caption_has_required_credit("Photo by a Tennessee hiker", "@tn_hiker"))
        self.assertFalse(caption_has_required_credit("", "@tn_hiker"))

    def test_composer_surfaces_credit_guard_and_restore_action(self):
        _submission, post, _variant = self.create_guarded_post(caption="Visit Machine Falls.")

        response = self.client.get(
            reverse("composer:compose_edit", kwargs={"workspace_id": self.workspace.id, "post_id": post.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Creator Rights &amp; Credit Guard")
        self.assertContains(response, "Required creator credit")
        self.assertContains(response, "Restore credit")
        self.assertEqual(response.context["composer_publish_guard"]["required_credit"], "@tn_hiker")

    def test_schedule_is_rejected_before_mutation_when_shared_or_override_credit_is_missing(self):
        _submission, post, variant = self.create_guarded_post(caption="Visit Machine Falls.")
        schedule_at = timezone.localtime() + timedelta(days=1)
        url = reverse(
            "composer:save_post_edit",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )
        payload = {
            "action": "schedule",
            "title": post.title,
            "caption": "Visit Machine Falls.",
            "tags": "",
            "selected_accounts": str(self.account.id),
            "scheduled_date": schedule_at.date().isoformat(),
            "scheduled_time": schedule_at.strftime("%H:%M"),
        }

        response = self.client.post(url, payload)

        self.assertEqual(response.status_code, 400)
        self.assertIn("ugc_rights", response.json()["errors"])
        post.refresh_from_db()
        variant.refresh_from_db()
        self.assertIsNone(post.scheduled_at)
        self.assertEqual(variant.status, PlatformPost.Status.DRAFT)
        blocked_event = AuditEvent.objects.get(action="ugc.schedule_guard_blocked")
        self.assertEqual(blocked_event.metadata["requested_action"], "schedule")
        self.assertEqual(blocked_event.metadata["blocker_codes"], ["credit_missing"])

        payload["caption"] = "Visit Machine Falls. @tn_hiker"
        payload[f"override_caption_{self.account.id}"] = "Instagram-only copy without attribution"
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, 400)
        self.assertIn("account caption", response.json()["errors"]["ugc_rights"])

    def test_single_account_transition_is_blocked_until_credit_is_restored(self):
        _submission, post, variant = self.create_guarded_post(caption="Visit Machine Falls.")
        url = reverse(
            "composer:transition_platform_post",
            kwargs={
                "workspace_id": self.workspace.id,
                "post_id": post.id,
                "platform_post_id": variant.id,
            },
        )

        blocked = self.client.post(url, {"target_status": "scheduled"})
        self.assertEqual(blocked.status_code, 400)
        variant.refresh_from_db()
        self.assertEqual(variant.status, PlatformPost.Status.DRAFT)

        post.caption = "Visit Machine Falls. Credit: @tn_hiker"
        post.save(update_fields=["caption", "updated_at"])
        allowed = self.client.post(url, {"target_status": "scheduled"})
        self.assertEqual(allowed.status_code, 200)
        variant.refresh_from_db()
        self.assertEqual(variant.status, PlatformPost.Status.SCHEDULED)

    def test_orchestration_routes_missing_credit_into_need_attention(self):
        _submission, post, variant = self.create_guarded_post()
        safe = build_orchestration(self.workspace)
        row = next(item for item in safe["rows"] if item["post"].id == post.id)
        self.assertTrue(row["publish_guard"]["is_safe"])
        self.assertFalse(row["action_needed"])

        variant.platform_specific_caption = "Custom caption with no creator credit."
        variant.save(update_fields=["platform_specific_caption", "updated_at"])
        unsafe = build_orchestration(self.workspace)
        row = next(item for item in unsafe["rows"] if item["post"].id == post.id)
        self.assertFalse(row["publish_guard"]["is_safe"])
        self.assertTrue(row["action_needed"])
        self.assertEqual(unsafe["counts"]["action"], 1)

        page = self.client.get(reverse("composer:orchestration", kwargs={"workspace_id": self.workspace.id}))
        self.assertContains(page, "Creator safeguard")
        self.assertContains(page, "Required creator credit")

    def test_final_dispatch_guard_holds_missing_credit_and_late_rights_revocation(self):
        submission, post, variant = self.create_guarded_post(caption="Visit Machine Falls.")
        variant.status = PlatformPost.Status.SCHEDULED
        variant.scheduled_at = timezone.now() - timedelta(minutes=1)
        variant.save(update_fields=["status", "scheduled_at", "updated_at"])

        with patch.object(PublishEngine, "_publish_platform_post") as dispatch:
            PublishEngine()._publish_post_group(post, [variant])

        dispatch.assert_not_called()
        variant.refresh_from_db()
        self.assertEqual(variant.status, PlatformPost.Status.ON_HOLD)
        self.assertIn("Creator Rights & Credit Guard", variant.publish_error)
        event = AuditEvent.objects.get(action="ugc.publish_guard_blocked")
        self.assertEqual(event.source, AuditEvent.Source.SYSTEM)
        self.assertEqual(event.metadata["blocker_codes"], ["credit_missing"])

        post.caption = "Visit Machine Falls. @tn_hiker"
        post.save(update_fields=["caption", "updated_at"])
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.REVOKED
        passport.revoked_at = timezone.now()
        passport.save(update_fields=["status", "revoked_at", "updated_at"])
        PlatformPost.objects.filter(id=variant.id).update(status=PlatformPost.Status.SCHEDULED, publish_error="")
        variant.refresh_from_db()

        with patch.object(PublishEngine, "_publish_platform_post") as dispatch:
            PublishEngine()._publish_post_group(post, [variant])

        dispatch.assert_not_called()
        variant.refresh_from_db()
        self.assertEqual(variant.status, PlatformPost.Status.ON_HOLD)
        latest = AuditEvent.objects.filter(action="ugc.publish_guard_blocked").latest("created_at")
        self.assertEqual(latest.metadata["blocker_codes"], ["rights_blocked"])

    def test_non_ugc_posts_remain_unaffected(self):
        post = Post.objects.create(workspace=self.workspace, author=self.user, caption="Normal branded post")
        variant = PlatformPost.objects.create(post=post, social_account=self.account)

        result = post_publish_preflight(self.workspace, post, [variant])

        self.assertFalse(result["is_ugc"])
        self.assertTrue(result["is_safe"])
        self.assertEqual(result["blockers"], [])
