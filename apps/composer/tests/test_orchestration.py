from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import AuditEvent, ContentPerformanceProfile, UGCRightsPassport, UGCSubmission
from apps.social_accounts.models import SocialAccount

from ..models import PlatformPost, Post


class MultiAccountOrchestrationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="orchestration@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.instagram = self._account("instagram_login", "ig", "TN Game Instagram")
        self.facebook = self._account("facebook", "fb", "TN Game Facebook")
        self.etsu = self._account("instagram", "etsu", "ETSU Pride")
        self.client.force_login(self.user)

    def _account(self, platform, platform_id, name):
        return SocialAccount.objects.create(
            workspace=self.workspace,
            platform=platform,
            account_platform_id=platform_id,
            account_name=name,
            account_handle=platform_id,
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )

    def _post(self, *, title="Waterfall weekend"):
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title=title,
            caption="Plan a Tennessee waterfall weekend.",
        )
        PlatformPost.objects.create(post=post, social_account=self.instagram, status=PlatformPost.Status.DRAFT)
        return post

    def test_dashboard_is_server_rendered_and_paginates_twelve(self):
        for index in range(13):
            self._post(title=f"Idea {index}")

        response = self.client.get(reverse("composer:orchestration", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Multi-account Orchestration")
        self.assertContains(response, "TN Game Facebook")
        self.assertEqual(len(response.context["orchestration_rows"]), 12)
        self.assertEqual(response.context["orchestration_page"].paginator.num_pages, 2)

    def test_add_variant_reuses_platform_post_and_writes_audit_event(self):
        post = self._post()
        url = reverse(
            "composer:add_orchestration_variant",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        response = self.client.post(url, {"account_id": self.facebook.id})

        self.assertEqual(response.status_code, 302)
        variant = PlatformPost.objects.get(post=post, social_account=self.facebook)
        self.assertEqual(variant.status, PlatformPost.Status.DRAFT)
        self.assertTrue(
            AuditEvent.objects.filter(
                action="composer.orchestration_variant_added",
                target_id=str(variant.id),
            ).exists()
        )

    def test_add_variant_honors_ugc_rights_account_allowlist(self):
        post = self._post(title="Community waterfall")
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            title="Community waterfall",
            consent_confirmed=True,
            consent_at=timezone.now(),
            metadata={"permission": {"status": "granted"}},
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.allowed_account_ids = [str(self.instagram.id)]
        passport.save(update_fields=["status", "allow_organic_social", "allowed_account_ids", "updated_at"])
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=post,
            source_submission=submission,
            source_type=ContentPerformanceProfile.SourceType.UGC,
            created_by=self.user,
        )
        url = reverse(
            "composer:add_orchestration_variant",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        response = self.client.post(url, {"account_id": self.facebook.id})

        self.assertEqual(response.status_code, 302)
        self.assertFalse(PlatformPost.objects.filter(post=post, social_account=self.facebook).exists())
        self.assertFalse(AuditEvent.objects.filter(action="composer.orchestration_variant_added").exists())

    def test_legacy_ugc_provenance_is_also_rights_checked(self):
        post = self._post(title="Legacy community waterfall")
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            title="Legacy community waterfall",
            consent_confirmed=True,
            consent_at=timezone.now(),
            metadata={"studio_post_ids": [str(post.id)], "permission": {"status": "granted"}},
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.REVOKED
        passport.allow_organic_social = True
        passport.save(update_fields=["status", "allow_organic_social", "updated_at"])
        url = reverse(
            "composer:add_orchestration_variant",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        self.client.post(url, {"account_id": self.facebook.id})

        self.assertFalse(PlatformPost.objects.filter(post=post, social_account=self.facebook).exists())

    def test_stagger_only_retimes_scheduled_variants_and_is_audited(self):
        post = self._post()
        anchor = timezone.now() + timedelta(days=2)
        first = post.platform_posts.get(social_account=self.instagram)
        first.status = PlatformPost.Status.SCHEDULED
        first.scheduled_at = anchor
        first.save(update_fields=["status", "scheduled_at", "updated_at"])
        second = PlatformPost.objects.create(
            post=post,
            social_account=self.facebook,
            status=PlatformPost.Status.SCHEDULED,
            scheduled_at=anchor,
        )
        draft = PlatformPost.objects.create(
            post=post,
            social_account=self.etsu,
            status=PlatformPost.Status.DRAFT,
        )
        url = reverse(
            "composer:stagger_orchestration",
            kwargs={"workspace_id": self.workspace.id, "post_id": post.id},
        )

        response = self.client.post(url, {"spacing_minutes": "60"})

        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual(abs(second.scheduled_at - first.scheduled_at), timedelta(hours=1))
        self.assertIsNone(draft.scheduled_at)
        post.refresh_from_db()
        self.assertEqual(post.scheduled_at, min(first.scheduled_at, second.scheduled_at))
        self.assertTrue(AuditEvent.objects.filter(action="composer.orchestration_rollout_staggered").exists())
