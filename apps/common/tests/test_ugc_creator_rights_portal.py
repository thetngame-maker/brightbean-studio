from datetime import timedelta

from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import AuditEvent, UGCCreator, UGCCreatorRightsRequest, UGCRightsPassport, UGCSubmission
from ..ugc_permissions import get_permission


class UGCCreatorRightsPortalTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="rights-portal@example.com",
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
            "contributor_handle": "tn_hiker",
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

    def create_request(self, submission, **payload):
        url = reverse(
            "ugc:create_creator_rights_request",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )
        values = {
            "allow_organic_social": "1",
            "allow_website": "1",
            "credit_required": "1",
            "return_to": reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id}),
        }
        values.update(payload)
        response = self.client.post(url, values)
        return response, UGCCreatorRightsRequest.objects.filter(submission=submission).first()

    def public_url(self, rights_request):
        return reverse("creator_rights_public:respond", kwargs={"token": rights_request.request_token})

    def test_staff_creates_encrypted_reusable_link_visible_in_mobile_and_passport(self):
        submission = self.create_submission()

        response, rights_request = self.create_request(submission)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(rights_request.status, UGCCreatorRightsRequest.Status.PENDING)
        self.assertTrue(rights_request.allow_organic_social)
        self.assertTrue(rights_request.allow_website)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT request_token FROM common_ugc_creator_rights_request WHERE id = %s",
                [str(rights_request.id).replace("-", "")],
            )
            stored_token = cursor.fetchone()[0]
        self.assertNotEqual(stored_token, rights_request.request_token)
        self.assertNotIn(rights_request.request_token, stored_token)

        event = AuditEvent.objects.get(action="ugc.creator_rights_request_created")
        self.assertNotIn("token", str(event.metadata).lower())
        queue = self.client.get(
            reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id}),
            {"tab": "discovered"},
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile/15E148",
        )
        self.assertContains(queue, "Rights link ready")
        self.assertContains(queue, self.public_url(rights_request))
        self.assertNotContains(queue, "IntersectionObserver")

        passport = self.client.get(
            reverse(
                "ugc:rights_passport",
                kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
            )
        )
        self.assertContains(passport, "Creator request history")
        self.assertContains(passport, "Copy link")

    def test_public_creator_grant_updates_canonical_permission_and_exact_scopes(self):
        submission = self.create_submission()
        _response, rights_request = self.create_request(submission)
        self.client.logout()

        page = self.client.get(self.public_url(rights_request))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Choose allowed usage")
        self.assertContains(page, "Organic social")
        self.assertContains(page, "TN Game website")
        self.assertEqual(page["Cache-Control"], "no-store, private")
        self.assertEqual(page["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", page["X-Robots-Tag"])
        self.assertNotContains(page, "IntersectionObserver")

        response = self.client.post(
            self.public_url(rights_request),
            {
                "action": "granted",
                "consent_confirmed": "1",
                "scopes": ["organic_social"],
                "credit_text": "@tn_hiker",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Permission recorded")
        submission.refresh_from_db()
        submission.creator.refresh_from_db()
        submission.rights_passport.refresh_from_db()
        rights_request.refresh_from_db()
        self.assertEqual(get_permission(submission.metadata)["status"], "granted")
        self.assertTrue(submission.consent_confirmed)
        self.assertEqual(submission.consent_version, "creator-rights-portal-v1")
        self.assertEqual(submission.creator.relationship_stage, UGCCreator.RelationshipStage.PERMISSIONED)
        self.assertEqual(rights_request.status, UGCCreatorRightsRequest.Status.GRANTED)
        self.assertEqual(rights_request.granted_scopes, ["organic_social"])
        passport = submission.rights_passport
        self.assertEqual(passport.status, UGCRightsPassport.Status.GRANTED)
        self.assertTrue(passport.allow_organic_social)
        self.assertFalse(passport.allow_website)
        self.assertFalse(passport.allow_paid_ads)
        self.assertEqual(passport.credit_text, "@tn_hiker")
        self.assertIn(str(rights_request.id), passport.evidence_note)

        event = AuditEvent.objects.get(action="ugc.creator_rights_granted")
        self.assertIsNone(event.actor)
        self.assertIsNone(event.ip_address)
        self.assertEqual(event.source, AuditEvent.Source.API)
        self.assertNotIn("token", str(event.metadata).lower())
        self.client.force_login(self.user)
        pending = self.client.get(
            reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id}),
            {"tab": "pending"},
            HTTP_USER_AGENT="iPhone Mobile",
        )
        self.assertContains(pending, "Waterfall day")

    def test_public_grant_requires_explicit_consent_and_rejects_scope_tampering(self):
        submission = self.create_submission()
        _response, rights_request = self.create_request(submission, allow_website="")
        self.client.logout()

        missing_consent = self.client.post(
            self.public_url(rights_request),
            {"action": "granted", "scopes": ["organic_social"]},
        )
        self.assertEqual(missing_consent.status_code, 400)
        submission.refresh_from_db()
        self.assertFalse(submission.consent_confirmed)

        granted = self.client.post(
            self.public_url(rights_request),
            {
                "action": "granted",
                "consent_confirmed": "1",
                "scopes": ["organic_social", "paid_ads"],
            },
        )
        self.assertEqual(granted.status_code, 200)
        rights_request.refresh_from_db()
        self.assertEqual(rights_request.granted_scopes, ["organic_social"])

    def test_public_decline_clears_consent_and_rights_and_is_idempotent(self):
        submission = self.create_submission()
        _response, rights_request = self.create_request(submission)
        self.client.logout()

        first = self.client.post(self.public_url(rights_request), {"action": "declined"})
        second = self.client.post(self.public_url(rights_request), {"action": "declined"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        submission.refresh_from_db()
        submission.rights_passport.refresh_from_db()
        rights_request.refresh_from_db()
        self.assertEqual(get_permission(submission.metadata)["status"], "declined")
        self.assertFalse(submission.consent_confirmed)
        self.assertEqual(submission.consent_version, "")
        self.assertIsNone(submission.consent_at)
        self.assertEqual(submission.rights_passport.status, UGCRightsPassport.Status.DECLINED)
        self.assertFalse(submission.rights_passport.allow_organic_social)
        self.assertEqual(rights_request.status, UGCCreatorRightsRequest.Status.DECLINED)
        self.assertEqual(AuditEvent.objects.filter(action="ugc.creator_rights_declined").count(), 1)

    def test_replacement_expiry_manual_decision_and_do_not_contact_close_unsafe_paths(self):
        submission = self.create_submission()
        _response, first = self.create_request(submission)
        _response, second = self.create_request(submission)
        first.refresh_from_db()
        self.assertEqual(first.status, UGCCreatorRightsRequest.Status.SUPERSEDED)
        self.assertEqual(second.status, UGCCreatorRightsRequest.Status.PENDING)

        permission_url = reverse(
            "ugc:update_permission",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )
        self.client.post(permission_url, {"permission_status": "granted", "channel": "instagram"})
        second.refresh_from_db()
        self.assertEqual(second.status, UGCCreatorRightsRequest.Status.CANCELLED)

        do_not_contact = self.create_submission(contributor_handle="no_contact")
        creator = do_not_contact.creator
        creator.relationship_stage = UGCCreator.RelationshipStage.DO_NOT_CONTACT
        creator.save(update_fields=["relationship_stage", "updated_at"])
        _response, blocked = self.create_request(do_not_contact)
        self.assertIsNone(blocked)

        expiring = self.create_submission(contributor_handle="expired_creator")
        _response, expired = self.create_request(expiring)
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at", "updated_at"])
        self.client.logout()
        page = self.client.get(self.public_url(expired))
        expired.refresh_from_db()
        self.assertContains(page, "This request expired")
        self.assertEqual(expired.status, UGCCreatorRightsRequest.Status.EXPIRED)

    def test_scope_form_can_request_one_scope_and_workspace_boundary_is_enforced(self):
        submission = self.create_submission()
        _response, rights_request = self.create_request(
            submission,
            scope_form="1",
            allow_website="",
            credit_required="",
        )
        self.assertTrue(rights_request.allow_organic_social)
        self.assertFalse(rights_request.allow_website)
        self.assertFalse(rights_request.credit_required)

        other_user = User.objects.create_user(
            email="other-rights@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        other_workspace = other_user.workspace_memberships.select_related("workspace").get().workspace
        other_submission = self.create_submission(workspace=other_workspace, contributor_handle="other_workspace")
        cross_workspace = self.client.post(
            reverse(
                "ugc:create_creator_rights_request",
                kwargs={"workspace_id": self.workspace.id, "submission_id": other_submission.id},
            ),
            {"allow_organic_social": "1"},
        )
        self.assertEqual(cross_workspace.status_code, 404)
        self.assertEqual(UGCCreatorRightsRequest.objects.filter(submission=other_submission).count(), 0)
        self.assertEqual(self.client.get("/creator-rights/not-a-valid-token/").status_code, 404)
