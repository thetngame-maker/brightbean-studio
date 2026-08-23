from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import (
    UGCCreator,
    UGCCreatorCollaboration,
    UGCCreatorIdentity,
    UGCRightsPassport,
    UGCSubmission,
)
from apps.common.ugc_creator_collaboration_invites import create_collaboration_invite
from apps.common.ugc_creator_collaboration_milestones import collaboration_milestone_summary


class CreatorCollaborationMilestoneTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="collaboration-milestones@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.creator = UGCCreator.objects.create(workspace=self.workspace, display_name="Trail Creator")
        UGCCreatorIdentity.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            platform="instagram",
            handle="trailcreator",
            normalized_handle="trailcreator",
            profile_url="https://www.instagram.com/trailcreator/",
        )
        self.collaboration = UGCCreatorCollaboration.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            title="Foster Falls creator weekend",
            deliverables="One Reel and three story frames",
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            requested_rights=["organic_social", "website"],
            invite_message="Join us at Foster Falls.",
            content_due_at=timezone.now() + timedelta(days=7),
            created_by=self.user,
        )

    def create_rights_ready_submission(self):
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            title="Delivered Foster Falls Reel",
            consent_confirmed=True,
            consent_version="creator-rights-portal-v1",
            consent_at=timezone.now(),
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.allow_website = True
        passport.credit_required = True
        passport.credit_text = "@trailcreator"
        passport.granted_at = timezone.now()
        passport.save(
            update_fields=[
                "status",
                "allow_organic_social",
                "allow_website",
                "credit_required",
                "credit_text",
                "granted_at",
                "updated_at",
            ]
        )
        return submission

    def test_milestones_follow_canonical_status_and_surface_delivery_risk(self):
        draft = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(draft["completed_count"], 1)
        self.assertEqual(draft["progress_percent"], 17)
        self.assertEqual(draft["next"]["key"], "invited")

        self.collaboration.status = UGCCreatorCollaboration.Status.INVITED
        self.collaboration.invited_at = timezone.now()
        self.collaboration.save(update_fields=["status", "invited_at", "updated_at"])
        invited = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(invited["completed_count"], 2)
        self.assertEqual(invited["next"]["key"], "confirmed")

        self.collaboration.status = UGCCreatorCollaboration.Status.CONFIRMED
        self.collaboration.content_due_at = timezone.now() - timedelta(days=3)
        self.collaboration.save(update_fields=["status", "content_due_at", "updated_at"])
        overdue = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(overdue["completed_count"], 3)
        self.assertTrue(overdue["at_risk"])
        self.assertEqual(overdue["blocked"]["key"], "delivered")
        self.assertIn("overdue by 3 days", overdue["blocked"]["description"])

    def test_rights_milestone_uses_linked_passport_and_completion_status(self):
        submission = self.create_rights_ready_submission()
        self.collaboration.status = UGCCreatorCollaboration.Status.CONTENT_RECEIVED
        self.collaboration.invited_at = timezone.now()
        self.collaboration.submission = submission
        self.collaboration.save(update_fields=["status", "invited_at", "submission", "updated_at"])

        rights_ready = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(rights_ready["completed_count"], 5)
        self.assertFalse(rights_ready["at_risk"])
        self.assertEqual(rights_ready["next"]["key"], "completed")

        submission.rights_passport.allow_website = False
        submission.rights_passport.save(update_fields=["allow_website", "updated_at"])
        blocked = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(blocked["completed_count"], 4)
        self.assertTrue(blocked["at_risk"])
        self.assertEqual(blocked["blocked"]["key"], "rights")

        submission.rights_passport.allow_website = True
        submission.rights_passport.save(update_fields=["allow_website", "updated_at"])
        self.collaboration.status = UGCCreatorCollaboration.Status.COMPLETED
        self.collaboration.completed_at = timezone.now()
        self.collaboration.save(update_fields=["status", "completed_at", "updated_at"])
        completed = collaboration_milestone_summary(self.collaboration)
        self.assertEqual(completed["completed_count"], 6)
        self.assertEqual(completed["progress_percent"], 100)
        self.assertIsNone(completed["next"])

    def test_staff_queue_and_detail_render_server_side_milestone_progress(self):
        self.client.force_login(self.user)
        queue = self.client.get(
            reverse("ugc:creator_collaborations", kwargs={"workspace_id": self.workspace.id})
        )
        self.assertEqual(queue.status_code, 200)
        self.assertContains(queue, "Project milestones")
        self.assertContains(queue, "1 / 6")
        self.assertContains(queue, "Invitation sent")

        detail = self.client.get(
            reverse(
                "ugc:creator_collaboration_detail",
                kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
            )
        )
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "1 of 6 complete")
        self.assertContains(detail, "Rights cleared")
        self.assertContains(detail, "Collaboration complete")
        self.assertNotContains(detail, "IntersectionObserver")

    def test_creator_portal_shows_live_milestones_before_and_after_acceptance(self):
        invite, _superseded = create_collaboration_invite(
            self.collaboration,
            actor=self.user,
            expires_in_days=14,
        )
        public_url = reverse(
            "creator_collaboration_public:respond",
            kwargs={"token": invite.request_token},
        )
        self.client.logout()
        page = self.client.get(public_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Project milestones")
        self.assertContains(page, "1 / 6")
        self.assertContains(page, "Creator confirmed")

        accepted = self.client.post(
            public_url,
            {"action": "accepted", "agreement_confirmed": "1"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertContains(accepted, "Collaboration accepted")
        self.assertContains(accepted, "3 / 6")
        self.assertContains(accepted, "Content delivered")
        self.assertNotContains(accepted, "IntersectionObserver")
