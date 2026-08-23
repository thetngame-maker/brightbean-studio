from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import (
    AuditEvent,
    UGCCreator,
    UGCCreatorCollaboration,
    UGCCreatorCollaborationInvite,
    UGCCreatorIdentity,
    UGCCreatorTask,
)


class CreatorCollaborationPortalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="collaboration-portal@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)
        self.creator = UGCCreator.objects.create(
            workspace=self.workspace,
            display_name="Tennessee Hiker",
        )
        UGCCreatorIdentity.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            platform="instagram",
            handle="tn_hiker",
            normalized_handle="tn_hiker",
            profile_url="https://www.instagram.com/tn_hiker/",
        )
        self.collaboration = UGCCreatorCollaboration.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            title="Greeter Falls spring Reel",
            brief="Show the trail and safe overlooks.",
            deliverables="One vertical Reel and three story frames",
            offer="$250 and trail merchandise",
            target_type="top_sight",
            target_id="greeter-falls",
            target_label="Greeter Falls",
            target_url="https://thetngame.com/greeter-falls/",
            requested_rights=["organic_social", "website"],
            invite_message="We would love to collaborate.",
            content_due_at=timezone.now() + timedelta(days=10),
            created_by=self.user,
        )
        self.initial_task = UGCCreatorTask.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            collaboration=self.collaboration,
            kind=UGCCreatorTask.Kind.COLLABORATION,
            title="Send collaboration invite",
            due_at=timezone.now(),
            created_by=self.user,
        )

    def create_invite(self, **data):
        url = reverse(
            "ugc:create_creator_collaboration_invite",
            kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
        )
        response = self.client.post(url, {"expires_in_days": "14", **data})
        return response, UGCCreatorCollaborationInvite.objects.filter(collaboration=self.collaboration).first()

    @staticmethod
    def public_url(invite):
        return reverse("creator_collaboration_public:respond", kwargs={"token": invite.request_token})

    def test_staff_creates_encrypted_frozen_invite_and_replacement_link(self):
        response, first = self.create_invite()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(first.status, UGCCreatorCollaborationInvite.Status.PENDING)
        self.assertEqual(first.terms_snapshot["title"], "Greeter Falls spring Reel")
        self.assertEqual(first.terms_snapshot["requested_rights"], ["organic_social", "website"])
        self.assertEqual(len(first.terms_digest), 64)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT request_token, terms_snapshot FROM common_ugc_creator_collaboration_invite WHERE id = %s",
                [str(first.id).replace("-", "")],
            )
            raw_token, raw_terms = cursor.fetchone()
        self.assertNotIn(first.request_token, raw_token)
        self.assertNotIn("Greeter Falls spring Reel", raw_terms)

        detail = self.client.get(
            reverse(
                "ugc:creator_collaboration_detail",
                kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
            )
        )
        self.assertContains(detail, "Copy invite + link")
        self.assertContains(detail, self.public_url(first))

        _response, second = self.create_invite(expires_in_days="30")
        first.refresh_from_db()
        self.assertEqual(first.status, UGCCreatorCollaborationInvite.Status.SUPERSEDED)
        self.assertNotEqual(first.id, second.id)
        event = AuditEvent.objects.filter(action="ugc.creator_collaboration_invite_created").latest("created_at")
        self.assertEqual(event.metadata["superseded_count"], 1)
        self.assertNotIn("token", str(event.metadata).lower())

    def test_public_accept_requires_confirmation_and_advances_tasks(self):
        _response, invite = self.create_invite()
        self.client.logout()
        page = self.client.get(self.public_url(invite))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "One vertical Reel and three story frames")
        self.assertContains(page, "$250 and trail merchandise")
        self.assertContains(page, "does not grant reuse rights")
        self.assertEqual(page["Cache-Control"], "no-store, private")
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", page["X-Robots-Tag"])
        self.assertNotContains(page, "IntersectionObserver")

        missing_confirmation = self.client.post(self.public_url(invite), {"action": "accepted"})
        self.assertEqual(missing_confirmation.status_code, 400)
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.DRAFT)

        response = self.client.post(
            self.public_url(invite),
            {
                "action": "accepted",
                "agreement_confirmed": "1",
                "response_note": "This timing works for me.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Collaboration accepted")
        invite.refresh_from_db()
        self.collaboration.refresh_from_db()
        self.creator.refresh_from_db()
        self.initial_task.refresh_from_db()
        self.assertEqual(invite.status, UGCCreatorCollaborationInvite.Status.ACCEPTED)
        self.assertEqual(invite.response_note, "This timing works for me.")
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.CONFIRMED)
        self.assertIsNotNone(self.collaboration.invited_at)
        self.assertEqual(self.creator.relationship_stage, UGCCreator.RelationshipStage.CONTACTED)
        self.assertEqual(self.initial_task.status, UGCCreatorTask.Status.DONE)
        followup = UGCCreatorTask.objects.get(
            collaboration=self.collaboration,
            status=UGCCreatorTask.Status.OPEN,
        )
        self.assertIn("Check in on deliverables", followup.title)
        event = AuditEvent.objects.get(action="ugc.creator_collaboration_accepted")
        self.assertIsNone(event.actor)
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.source, AuditEvent.Source.API)
        self.assertTrue(event.metadata["has_response_note"])
        self.assertNotIn("timing works", str(event.metadata).lower())
        self.assertNotIn("token", str(event.metadata).lower())
        self.client.force_login(self.user)
        detail = self.client.get(
            reverse(
                "ugc:creator_collaboration_detail",
                kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
            )
        )
        self.assertContains(detail, "Creator response history")
        self.assertContains(detail, "This timing works for me.")
        self.client.logout()

        second_response = self.client.post(self.public_url(invite), {"action": "declined"})
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(AuditEvent.objects.filter(action="ugc.creator_collaboration_accepted").count(), 1)

    def test_decline_expiry_term_edits_and_manual_close_invalidate_links(self):
        _response, declined = self.create_invite()
        self.client.logout()
        response = self.client.post(
            self.public_url(declined),
            {"action": "declined", "response_note": "Not available this month."},
        )
        self.assertEqual(response.status_code, 200)
        self.collaboration.refresh_from_db()
        declined.refresh_from_db()
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.DECLINED)
        self.assertEqual(declined.status, UGCCreatorCollaborationInvite.Status.DECLINED)

        self.collaboration.status = UGCCreatorCollaboration.Status.DRAFT
        self.collaboration.save(update_fields=["status", "updated_at"])
        self.client.force_login(self.user)
        _response, expiring = self.create_invite()
        expiring.expires_at = timezone.now() - timedelta(seconds=1)
        expiring.save(update_fields=["expires_at", "updated_at"])
        self.client.logout()
        expired_page = self.client.get(self.public_url(expiring))
        expiring.refresh_from_db()
        self.assertContains(expired_page, "This invitation expired")
        self.assertEqual(expiring.status, UGCCreatorCollaborationInvite.Status.EXPIRED)

        self.client.force_login(self.user)
        _response, stale = self.create_invite()
        self.collaboration.deliverables = "Changed outside the collaboration workflow"
        self.collaboration.save(update_fields=["deliverables", "updated_at"])
        self.client.logout()
        self.client.post(
            self.public_url(stale),
            {"action": "accepted", "agreement_confirmed": "1"},
        )
        stale.refresh_from_db()
        self.collaboration.refresh_from_db()
        self.assertEqual(stale.status, UGCCreatorCollaborationInvite.Status.SUPERSEDED)
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.DRAFT)

        self.client.force_login(self.user)
        _response, edited = self.create_invite()
        update_url = reverse(
            "ugc:update_creator_collaboration",
            kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
        )
        self.client.post(
            update_url,
            {
                "action": "save",
                "title": "Updated spring Reel",
                "deliverables": "Two Reels",
                "target_key": "",
                "invite_message": "Updated invitation",
            },
        )
        edited.refresh_from_db()
        self.assertEqual(edited.status, UGCCreatorCollaborationInvite.Status.SUPERSEDED)

        _response, cancelled = self.create_invite()
        self.client.post(update_url, {"action": "cancel"})
        cancelled.refresh_from_db()
        self.assertEqual(cancelled.status, UGCCreatorCollaborationInvite.Status.CANCELLED)

    def test_do_not_contact_and_workspace_boundary_block_invite_creation(self):
        self.creator.relationship_stage = UGCCreator.RelationshipStage.DO_NOT_CONTACT
        self.creator.save(update_fields=["relationship_stage", "updated_at"])
        response, invite = self.create_invite()
        self.assertEqual(response.status_code, 302)
        self.assertIsNone(invite)

        other_user = User.objects.create_user(
            email="other-collaboration@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        other_workspace = other_user.workspace_memberships.select_related("workspace").get().workspace
        url = reverse(
            "ugc:create_creator_collaboration_invite",
            kwargs={"workspace_id": other_workspace.id, "collaboration_id": self.collaboration.id},
        )
        self.client.force_login(other_user)
        self.assertEqual(self.client.post(url).status_code, 404)
