from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post

from ..models import AuditEvent, UGCCreator, UGCCreatorIdentity, UGCRightsPassport, UGCSubmission
from ..ugc_creator_services import rights_can_use
from ..ugc_mobile_quality import approved_quality
from ..ugc_permissions import get_permission


class UGCCreatorRelationshipTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="creator-hub@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)

    def create_submission(self, **overrides):
        values = {
            "workspace": self.workspace,
            "kind": UGCSubmission.Kind.COMMUNITY_POST,
            "status": UGCSubmission.Status.PENDING,
            "source": UGCSubmission.Source.IMPORT,
            "contributor_name": "Tennessee Hiker",
            "contributor_handle": "TN_Hiker",
            "target_type": "top_sight",
            "target_id": "machine-falls",
            "target_label": "Machine Falls",
            "title": "Waterfall day",
            "body": "Machine Falls after the rain.",
            "metadata": {
                "provenance": {
                    "platform": "instagram",
                    "discovery_source": "saved_search",
                    "source_url": "https://www.instagram.com/p/example/",
                },
                "permission": {"status": "not_contacted"},
            },
        }
        values.update(overrides)
        return UGCSubmission.objects.create(**values)

    def test_same_platform_identity_reuses_one_creator_and_creates_passports(self):
        first = self.create_submission()
        second = self.create_submission(title="Another trail day", contributor_handle="@tn_hiker")

        self.assertEqual(first.creator_id, second.creator_id)
        self.assertEqual(UGCCreator.objects.count(), 1)
        self.assertEqual(UGCCreatorIdentity.objects.count(), 1)
        self.assertEqual(UGCRightsPassport.objects.count(), 2)
        self.assertEqual(first.rights_passport.status, UGCRightsPassport.Status.NOT_REQUESTED)

    def test_permission_updates_relationship_and_default_legacy_rights(self):
        submission = self.create_submission()
        permission_url = reverse(
            "ugc:update_permission",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )

        self.client.post(permission_url, {"permission_status": "requested", "channel": "instagram"})
        submission.refresh_from_db()
        submission.creator.refresh_from_db()
        submission.rights_passport.refresh_from_db()
        self.assertEqual(submission.creator.relationship_stage, UGCCreator.RelationshipStage.CONTACTED)
        self.assertEqual(submission.rights_passport.status, UGCRightsPassport.Status.REQUESTED)

        self.client.post(permission_url, {"permission_status": "granted", "channel": "instagram"})
        submission.refresh_from_db()
        submission.creator.refresh_from_db()
        submission.rights_passport.refresh_from_db()
        self.assertEqual(submission.creator.relationship_stage, UGCCreator.RelationshipStage.PERMISSIONED)
        self.assertEqual(submission.rights_passport.status, UGCRightsPassport.Status.GRANTED)
        self.assertTrue(submission.rights_passport.allow_organic_social)
        self.assertTrue(submission.rights_passport.allow_website)

    def test_creator_hub_and_profile_are_server_rendered(self):
        submission = self.create_submission()

        hub = self.client.get(reverse("ugc:creator_hub", kwargs={"workspace_id": self.workspace.id}))
        self.assertEqual(hub.status_code, 200)
        self.assertContains(hub, "Creator Relationships")
        self.assertContains(hub, "@TN_Hiker")

        detail = self.client.get(
            reverse(
                "ugc:creator_detail",
                kwargs={"workspace_id": self.workspace.id, "creator_id": submission.creator_id},
            )
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Content and rights")
        self.assertContains(detail, "Waterfall day")

    def test_do_not_contact_blocks_request_and_followup(self):
        submission = self.create_submission()
        creator = submission.creator
        creator.relationship_stage = UGCCreator.RelationshipStage.DO_NOT_CONTACT
        creator.save(update_fields=["relationship_stage", "updated_at"])
        permission_url = reverse(
            "ugc:update_permission",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )

        self.client.post(permission_url, {"permission_status": "requested", "channel": "instagram"})
        submission.refresh_from_db()
        self.assertEqual(get_permission(submission.metadata)["status"], "not_contacted")

        submission.metadata["permission"] = {"status": "requested", "updated_at": timezone.now().isoformat()}
        submission.save(update_fields=["metadata", "updated_at"])
        followup_url = reverse(
            "ugc:log_followup",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )
        self.client.post(followup_url, {"channel": "instagram"})
        submission.refresh_from_db()
        self.assertNotIn("outreach", submission.metadata)

    def test_rights_update_is_audited_and_enforced_before_drafting(self):
        submission = self.create_submission(
            status=UGCSubmission.Status.APPROVED,
            consent_confirmed=True,
            consent_version="creator-permission-v1",
            consent_at=timezone.now(),
        )
        rights_url = reverse(
            "ugc:update_rights_passport",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )
        self.client.post(
            rights_url,
            {
                "status": "granted",
                "allow_website": "on",
                "credit_required": "on",
                "credit_text": "@tn_hiker",
                "evidence_url": "https://www.instagram.com/p/example/",
            },
        )
        submission.refresh_from_db()
        submission.rights_passport.refresh_from_db()
        allowed, reason = rights_can_use(submission)
        self.assertFalse(allowed)
        self.assertIn("organic social", reason)
        self.assertTrue(approved_quality(submission)["needs_check"])
        self.assertEqual(approved_quality(submission)["kind"], "rights")

        draft_url = reverse(
            "ugc:use_in_post",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )
        self.client.post(draft_url)
        self.assertEqual(Post.objects.count(), 0)
        self.assertTrue(
            AuditEvent.objects.filter(action="ugc.rights_passport_updated", target_id=str(submission.id)).exists()
        )

    def test_expired_rights_are_not_usable(self):
        submission = self.create_submission(
            status=UGCSubmission.Status.APPROVED,
            consent_confirmed=True,
            consent_at=timezone.now(),
        )
        passport = submission.rights_passport
        passport.expires_at = timezone.now() - timedelta(minutes=1)
        passport.save(update_fields=["expires_at", "updated_at"])

        allowed, reason = rights_can_use(submission)
        self.assertFalse(allowed)
        self.assertIn("expired", reason)

    def test_opportunity_radar_identifies_and_promotes_trusted_candidate(self):
        self.create_submission(
            consent_confirmed=True,
            consent_at=timezone.now(),
            contributor_handle="trusted_candidate",
            title="First granted post",
        )
        second = self.create_submission(
            consent_confirmed=True,
            consent_at=timezone.now(),
            contributor_handle="trusted_candidate",
            title="Second granted post",
        )
        creator = second.creator

        radar_url = reverse("ugc:creator_opportunities", kwargs={"workspace_id": self.workspace.id})
        response = self.client.get(radar_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Opportunity Radar")
        self.assertContains(response, "Ready for trusted")
        self.assertContains(response, "@trusted_candidate")
        searched = self.client.get(radar_url, {"q": "trusted_candidate"})
        self.assertEqual(searched.status_code, 200)
        self.assertContains(searched, "@trusted_candidate")

        promote_url = reverse(
            "ugc:promote_creator",
            kwargs={"workspace_id": self.workspace.id, "creator_id": creator.id},
        )
        response = self.client.post(
            promote_url,
            {"target_stage": "trusted", "return_to": f"{radar_url}?queue=trusted"},
        )
        self.assertRedirects(response, f"{radar_url}?queue=trusted", fetch_redirect_response=False)
        creator.refresh_from_db()
        self.assertEqual(creator.relationship_stage, UGCCreator.RelationshipStage.TRUSTED)
        self.assertTrue(AuditEvent.objects.filter(action="ugc.creator_promoted", target_id=str(creator.id)).exists())

    def test_opportunity_radar_finds_rising_prospect_from_stored_metrics(self):
        metadata = {
            "provenance": {
                "platform": "instagram",
                "discovery_source": "saved_search",
                "source_url": "https://www.instagram.com/p/rising/",
            },
            "permission": {"status": "not_contacted"},
            "discovery_import": {"like_count": 400, "comment_count": 20, "view_count": 10000},
        }
        self.create_submission(contributor_handle="rising_hiker", metadata=metadata, title="Rising post one")
        self.create_submission(contributor_handle="rising_hiker", metadata=metadata, title="Rising post two")

        response = self.client.get(
            reverse("ugc:creator_opportunities", kwargs={"workspace_id": self.workspace.id}),
            {"queue": "rising"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rising prospect")
        self.assertContains(response, "1,400 engagement signal")

    def test_opportunity_promotion_rejects_skipping_relationship_stages(self):
        submission = self.create_submission(contributor_handle="new_prospect")
        promote_url = reverse(
            "ugc:promote_creator",
            kwargs={"workspace_id": self.workspace.id, "creator_id": submission.creator_id},
        )

        self.client.post(promote_url, {"target_stage": "partner"})
        submission.creator.refresh_from_db()
        self.assertEqual(submission.creator.relationship_stage, UGCCreator.RelationshipStage.PROSPECT)
        self.assertFalse(AuditEvent.objects.filter(action="ugc.creator_promoted").exists())
