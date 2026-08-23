from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import (
    AuditEvent,
    UGCCreator,
    UGCCreatorCollaboration,
    UGCCreatorCollaborationDelivery,
    UGCCreatorCollaborationInvite,
    UGCCreatorIdentity,
    UGCCreatorRightsRequest,
    UGCCreatorTask,
    UGCRightsPassport,
    UGCSubmission,
)
from apps.common.ugc_creator_collaboration_invites import create_collaboration_invite
from apps.common.ugc_permissions import GRANTED, get_permission
from apps.common.ugc_provenance import get_provenance
from apps.media_library.models import MediaAsset


class CreatorCollaborationDeliveryTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="creator-deliveries@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.creator = UGCCreator.objects.create(
            workspace=self.workspace,
            display_name="Trail Creator",
            preferred_credit="@trailcreator",
        )
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
            brief="Show the trail and safe overlooks.",
            deliverables="One Reel and three story frames",
            offer="$250 and trail merchandise",
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            target_url="https://thetngame.com/foster-falls/",
            requested_rights=["organic_social", "website"],
            invite_message="Join us at Foster Falls.",
            content_due_at=timezone.now() + timedelta(days=7),
            created_by=self.user,
        )
        self.invite, _superseded = create_collaboration_invite(
            self.collaboration,
            actor=self.user,
            expires_in_days=14,
        )
        self.public_url = reverse(
            "creator_collaboration_public:respond",
            kwargs={"token": self.invite.request_token},
        )
        accepted = self.client.post(
            self.public_url,
            {"action": "accepted", "agreement_confirmed": "1"},
        )
        self.assertEqual(accepted.status_code, 200)
        self.collaboration.refresh_from_db()

    @staticmethod
    def image_upload(name="delivery.jpg"):
        return SimpleUploadedFile(name, b"\xff\xd8\xff\xe0creator-image", content_type="image/jpeg")

    @staticmethod
    def video_upload(name="delivery.mp4"):
        return SimpleUploadedFile(
            name,
            b"\x00\x00\x00\x18ftypisomcreator-video",
            content_type="video/mp4",
        )

    def submit_delivery(self, **data):
        payload = {
            "action": "submit_delivery",
            "deliverables_confirmed": "1",
            "source_url": "https://www.instagram.com/reel/foster-delivery/",
            "creator_note": "The Reel and story frames are ready.",
            **data,
        }
        return self.client.post(self.public_url, payload)

    def test_creator_delivery_creates_canonical_ugc_media_revision_and_review_task(self):
        response = self.submit_delivery(media_files=[self.image_upload(), self.video_upload()])

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Delivery received")
        self.assertContains(response, "Review in progress")
        delivery = UGCCreatorCollaborationDelivery.objects.get(collaboration=self.collaboration)
        self.assertEqual(delivery.status, UGCCreatorCollaborationDelivery.Status.SUBMITTED)
        self.assertEqual(delivery.revision_number, 1)
        self.assertEqual(delivery.attachments.count(), 2)
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.CONFIRMED)
        self.assertEqual(self.collaboration.submission_id, delivery.submission_id)

        submission = delivery.submission
        self.assertEqual(submission.status, UGCSubmission.Status.PENDING)
        self.assertEqual(submission.creator, self.creator)
        self.assertEqual(submission.media_asset_id, delivery.attachments.get(position=0).media_asset_id)
        self.assertFalse(submission.consent_confirmed)
        self.assertEqual(submission.rights_passport.status, UGCRightsPassport.Status.NOT_REQUESTED)
        self.assertEqual(get_provenance(submission.metadata)["platform"], "instagram")
        self.assertEqual(get_provenance(submission.metadata)["discovery_source"], "manual")
        self.assertEqual(submission.metadata["creator_delivery"]["latest_revision"], 1)
        self.assertTrue(
            UGCCreatorTask.objects.filter(
                collaboration=self.collaboration,
                status=UGCCreatorTask.Status.OPEN,
                title__startswith="Review creator delivery",
            ).exists()
        )
        event = AuditEvent.objects.get(action="ugc.creator_collaboration_delivery_submitted")
        self.assertIsNone(event.actor)
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.metadata["file_count"], 2)

        duplicate = self.submit_delivery(source_url="https://www.instagram.com/reel/duplicate/")
        self.assertEqual(duplicate.status_code, 400)
        self.assertContains(duplicate, "already waiting", status_code=400)
        self.assertEqual(UGCCreatorCollaborationDelivery.objects.count(), 1)
        self.assertNotContains(response, "IntersectionObserver")

    def test_revision_acceptance_prepares_rights_and_creator_can_grant_them(self):
        first = self.submit_delivery(media_files=[])
        self.assertEqual(first.status_code, 200)
        delivery_one = UGCCreatorCollaborationDelivery.objects.get(collaboration=self.collaboration)
        submission_id = delivery_one.submission_id

        self.client.force_login(self.user)
        review_url = reverse(
            "ugc:review_creator_collaboration_delivery",
            kwargs={"workspace_id": self.workspace.id, "delivery_id": delivery_one.id},
        )
        revision = self.client.post(
            review_url,
            {
                "action": "request_revision",
                "review_note": "Please include the trailhead safety sign in the opening frame.",
            },
        )
        self.assertEqual(revision.status_code, 302)
        delivery_one.refresh_from_db()
        self.assertEqual(delivery_one.status, UGCCreatorCollaborationDelivery.Status.REVISION_REQUESTED)

        self.client.logout()
        portal = self.client.get(self.public_url)
        self.assertContains(portal, "Please include the trailhead safety sign")
        self.assertContains(portal, "Send revised delivery")
        revised = self.submit_delivery(
            source_url="https://www.instagram.com/reel/foster-delivery-v2/",
            creator_note="Updated opening frame included.",
            media_files=[self.image_upload("revision-two.jpg")],
        )
        self.assertEqual(revised.status_code, 200)
        delivery_two = UGCCreatorCollaborationDelivery.objects.get(
            collaboration=self.collaboration,
            revision_number=2,
        )
        self.assertEqual(delivery_two.submission_id, submission_id)
        self.assertEqual(delivery_one.review_note, "Please include the trailhead safety sign in the opening frame.")

        self.client.force_login(self.user)
        accept_url = reverse(
            "ugc:review_creator_collaboration_delivery",
            kwargs={"workspace_id": self.workspace.id, "delivery_id": delivery_two.id},
        )
        accepted = self.client.post(accept_url, {"action": "accept"})
        self.assertEqual(accepted.status_code, 302)
        delivery_two.refresh_from_db()
        self.collaboration.refresh_from_db()
        self.assertEqual(delivery_two.status, UGCCreatorCollaborationDelivery.Status.ACCEPTED)
        self.assertEqual(self.collaboration.status, UGCCreatorCollaboration.Status.CONTENT_RECEIVED)
        rights_request = UGCCreatorRightsRequest.objects.get(submission_id=submission_id)
        self.assertEqual(rights_request.status, UGCCreatorRightsRequest.Status.PENDING)
        self.assertTrue(rights_request.allow_organic_social)
        self.assertTrue(rights_request.allow_website)
        self.assertFalse(rights_request.allow_email)

        self.client.logout()
        project = self.client.get(self.public_url)
        self.assertContains(project, "Delivery accepted")
        self.assertContains(project, "Review usage rights")
        rights_url = reverse(
            "creator_rights_public:respond",
            kwargs={"token": rights_request.request_token},
        )
        self.assertContains(project, rights_url)
        granted = self.client.post(
            rights_url,
            {
                "action": "granted",
                "scopes": ["organic_social", "website"],
                "credit_text": "@trailcreator",
                "consent_confirmed": "1",
            },
        )
        self.assertEqual(granted.status_code, 200)
        submission = UGCSubmission.objects.get(id=submission_id)
        self.assertEqual(submission.status, UGCSubmission.Status.PENDING)
        self.assertEqual(get_permission(submission.metadata)["status"], GRANTED)
        self.assertEqual(submission.rights_passport.status, UGCRightsPassport.Status.GRANTED)
        self.assertTrue(
            UGCCreatorTask.objects.filter(
                collaboration=self.collaboration,
                status=UGCCreatorTask.Status.OPEN,
                title__startswith="Complete collaboration",
            ).exists()
        )
        project_after_rights = self.client.get(self.public_url)
        self.assertContains(project_after_rights, "5 / 6")
        self.assertContains(project_after_rights, "Usage permission recorded")

    def test_invalid_delivery_upload_is_rejected_without_creating_records(self):
        malicious = SimpleUploadedFile(
            "fake.jpg",
            b"<html><script>alert('x')</script></html>",
            content_type="image/jpeg",
        )
        response = self.submit_delivery(source_url="", media_files=[malicious])
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Unsupported or unrecognised file type", status_code=400)
        self.assertFalse(UGCCreatorCollaborationDelivery.objects.exists())
        self.assertFalse(UGCSubmission.objects.exists())
        self.assertFalse(MediaAsset.objects.filter(workspace=self.workspace).exists())

        missing_confirmation = self.submit_delivery(
            source_url="https://www.instagram.com/reel/unconfirmed/",
            deliverables_confirmed="0",
            media_files=[],
        )
        self.assertEqual(missing_confirmation.status_code, 400)
        self.assertContains(missing_confirmation, "Confirm that this submission includes", status_code=400)

    def test_staff_can_replace_an_expired_rights_request_without_duplication(self):
        self.submit_delivery(media_files=[])
        delivery = UGCCreatorCollaborationDelivery.objects.get(collaboration=self.collaboration)
        self.client.force_login(self.user)
        review_url = reverse(
            "ugc:review_creator_collaboration_delivery",
            kwargs={"workspace_id": self.workspace.id, "delivery_id": delivery.id},
        )
        self.client.post(review_url, {"action": "accept"})
        original = UGCCreatorRightsRequest.objects.get(submission=delivery.submission)
        original.expires_at = timezone.now() - timedelta(minutes=1)
        original.save(update_fields=["expires_at", "updated_at"])

        detail_url = reverse(
            "ugc:creator_collaboration_detail",
            kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
        )
        detail = self.client.get(detail_url)
        original.refresh_from_db()
        self.assertEqual(original.status, UGCCreatorRightsRequest.Status.EXPIRED)
        self.assertContains(detail, "Create replacement rights request")

        replacement_response = self.client.post(
            review_url,
            {"action": "refresh_rights", "return_to": detail_url},
        )
        self.assertRedirects(replacement_response, detail_url)
        requests = list(UGCCreatorRightsRequest.objects.filter(submission=delivery.submission).order_by("created_at"))
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].status, UGCCreatorRightsRequest.Status.EXPIRED)
        self.assertEqual(requests[1].status, UGCCreatorRightsRequest.Status.PENDING)
        self.assertNotEqual(requests[0].request_token, requests[1].request_token)
        self.assertTrue(requests[1].allow_organic_social)
        self.assertTrue(requests[1].allow_website)

        duplicate = self.client.post(review_url, {"action": "refresh_rights"})
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(UGCCreatorRightsRequest.objects.filter(submission=delivery.submission).count(), 2)

    def test_staff_detail_is_mobile_lightweight_and_accepted_terms_are_frozen(self):
        self.submit_delivery(media_files=[])
        self.client.force_login(self.user)
        detail_url = reverse(
            "ugc:creator_collaboration_detail",
            kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
        )
        detail = self.client.get(detail_url)
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "Creator delivery")
        self.assertContains(detail, "Revision 1")
        self.assertContains(detail, "Accept delivery + prepare rights")
        self.assertContains(detail, "Copy creator project link")
        self.assertContains(detail, "Accepted terms are frozen")
        self.assertNotContains(detail, "IntersectionObserver")

        original_title = self.collaboration.title
        update_url = reverse(
            "ugc:update_creator_collaboration",
            kwargs={"workspace_id": self.workspace.id, "collaboration_id": self.collaboration.id},
        )
        blocked = self.client.post(
            update_url,
            {
                "action": "save",
                "title": "Changed accepted title",
                "deliverables": "Different work",
            },
        )
        self.assertEqual(blocked.status_code, 302)
        self.collaboration.refresh_from_db()
        self.assertEqual(self.collaboration.title, original_title)
        self.assertEqual(UGCCreatorCollaborationInvite.objects.get(id=self.invite.id).status, "accepted")
