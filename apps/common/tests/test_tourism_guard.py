from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.command_center import build_command_center
from apps.composer.models import PlatformPost, Post
from apps.publisher.engine import PublishEngine
from apps.social_accounts.models import SocialAccount

from ..models import AuditEvent, ContentPerformanceProfile, TourismGuardReview, TourismGuardRule
from ..tourism_guard import blocking_findings_for_post, findings_for_post


class TourismGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="tourism-guard@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.workspace.discovery_searches = [
            {
                "id": "guard-target",
                "name": "Foster Falls",
                "target_type": "top_sight",
                "target_id": "foster-falls",
                "target_label": "Foster Falls",
            }
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="tourism-guard-instagram",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def create_post(self, caption, *, status=PlatformPost.Status.DRAFT, target=True):
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Foster Falls guide",
            caption=caption,
        )
        platform_post = PlatformPost.objects.create(
            post=post,
            social_account=self.account,
            status=status,
            scheduled_at=timezone.now(),
        )
        if target:
            ContentPerformanceProfile.objects.create(
                workspace=self.workspace,
                post=post,
                source_type=ContentPerformanceProfile.SourceType.BRANDED,
                target_type="top_sight",
                target_id="foster-falls",
                target_label="Foster Falls",
                created_by=self.user,
                updated_by=self.user,
            )
        return post, platform_post

    def custom_rule(self, *, severity=TourismGuardRule.Severity.BLOCKER, triggers=None):
        return TourismGuardRule.objects.create(
            workspace=self.workspace,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            kind=TourismGuardRule.Kind.ACCESS,
            severity=severity,
            title="Trail closure check",
            guidance="Confirm the current closure before publication.",
            trigger_phrases=triggers or [],
            source_url="https://tnstateparks.com/parks/foster-falls",
            source_label="Tennessee State Parks",
            created_by=self.user,
            updated_by=self.user,
        )

    def test_builtin_blocker_is_conservative_about_prohibition_context(self):
        unsafe, _variant = self.create_post("Cliff jumping is the best way to cool off.", target=False)
        safe, _safe_variant = self.create_post("Cliff jumping is prohibited. Do not attempt it.", target=False)

        self.assertEqual(len(blocking_findings_for_post(self.workspace, unsafe.id)), 1)
        self.assertEqual(blocking_findings_for_post(self.workspace, safe.id), [])

    def test_target_rule_verification_is_invalidated_by_content_edit(self):
        rule = self.custom_rule(triggers=["trail is open"])
        post, _variant = self.create_post("The trail is open this weekend.")
        _post, findings, verified = findings_for_post(self.workspace, post.id)
        finding = next(item for item in findings if item["rule_key"] == f"rule:{rule.id}")
        TourismGuardReview.objects.create(
            workspace=self.workspace,
            post=post,
            rule_key=finding["rule_key"],
            finding_fingerprint=finding["fingerprint"],
            note="Confirmed against the official park alert.",
            reviewed_by=self.user,
        )

        _post, findings, verified = findings_for_post(self.workspace, post.id)
        self.assertFalse(findings)
        self.assertEqual(len(verified), 1)

        post.caption = "The trail is open this weekend. Parking is always available."
        post.save(update_fields=["caption", "updated_at"])
        _post, findings, verified = findings_for_post(self.workspace, post.id)
        self.assertTrue(any(item["rule_key"] == f"rule:{rule.id}" for item in findings))
        self.assertFalse(verified)

    def test_blocker_override_requires_reason_and_writes_audit(self):
        post, _variant = self.create_post("Try cliff jumping at the waterfall.", target=False)
        url = reverse(
            "ugc:verify_tourism_guard_finding",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )
        rule_key = blocking_findings_for_post(self.workspace, post.id)[0]["rule_key"]

        self.client.post(url, {"rule_key": rule_key, "note": "short"})
        self.assertFalse(TourismGuardReview.objects.filter(post=post).exists())

        self.client.post(url, {"rule_key": rule_key, "note": "Verified as a prohibition in the full visual."})
        self.assertTrue(TourismGuardReview.objects.filter(post=post, rule_key=rule_key).exists())
        self.assertTrue(
            AuditEvent.objects.filter(action="tourism_guard.finding_verified", target_id=str(post.id)).exists()
        )

    def test_source_backed_rule_creation_reuses_existing_target_catalog(self):
        url = reverse("ugc:create_tourism_guard_rule", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            url,
            {
                "target_key": "top_sight::foster-falls",
                "kind": "seasonal",
                "severity": "warning",
                "title": "Seasonal bridge access",
                "guidance": "Verify the seasonal bridge notice.",
                "source_url": "https://tnstateparks.com/parks/foster-falls",
                "source_label": "Tennessee State Parks",
            },
        )

        self.assertEqual(response.status_code, 302)
        rule = TourismGuardRule.objects.get(title="Seasonal bridge access")
        self.assertEqual(rule.target_id, "foster-falls")
        self.assertTrue(AuditEvent.objects.filter(action="tourism_guard.rule_created", target_id=str(rule.id)).exists())

    def test_publisher_holds_entire_due_group_before_provider_dispatch(self):
        post, first = self.create_post("Cliff jumping is encouraged here.", status=PlatformPost.Status.SCHEDULED)
        second_account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="tourism-guard-facebook",
            account_name="TN Game Facebook",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        second = PlatformPost.objects.create(
            post=post,
            social_account=second_account,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=timezone.now(),
        )

        with patch.object(PublishEngine, "_publish_platform_post") as publish:
            PublishEngine()._publish_post_group(post, [first, second])

        self.assertFalse(publish.called)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, PlatformPost.Status.ON_HOLD)
        self.assertEqual(second.status, PlatformPost.Status.ON_HOLD)
        self.assertIn("Tourism Guard", first.publish_error)
        self.assertTrue(
            AuditEvent.objects.filter(action="tourism_guard.publish_blocked", target_id=str(post.id)).exists()
        )

        rule_key = blocking_findings_for_post(self.workspace, post.id)[0]["rule_key"]
        verify_url = reverse(
            "ugc:verify_tourism_guard_finding",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )
        self.client.post(
            verify_url,
            {"rule_key": rule_key, "note": "Verified as safe in the complete visual and final caption."},
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.status, PlatformPost.Status.SCHEDULED)
        self.assertEqual(second.status, PlatformPost.Status.SCHEDULED)
        self.assertTrue(AuditEvent.objects.filter(action="tourism_guard.publish_released").exists())

    def test_command_center_surfaces_guard_findings(self):
        self.create_post("This overlook is completely safe in all conditions.", target=False)

        briefing = build_command_center(self.workspace, permissions={})

        self.assertEqual(briefing["counts"]["guard"], 1)
        action = next(item for item in briefing["actions"] if item["kind"] == "Tourism Guard")
        self.assertIn("warning", action["detail"])

    def test_mobile_guard_queue_is_server_rendered_and_paginates_twelve(self):
        for index in range(13):
            self.create_post(f"Cliff jumping guide number {index}.", target=False)

        response = self.client.get(reverse("ugc:tourism_guard", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Tourism Guard")
        self.assertEqual(len(response.context["guard_rows"]), 12)
        self.assertEqual(response.context["guard_page"].paginator.num_pages, 2)
        self.assertNotContains(response, "IntersectionObserver")
