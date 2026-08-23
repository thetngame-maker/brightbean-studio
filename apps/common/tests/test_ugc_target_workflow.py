from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import AuditEvent, UGCSubmission
from ..ugc_discovery_search_views import get_saved_search
from ..ugc_mobile_quality import approved_quality


class ApprovedQualityTests(SimpleTestCase):
    def _submission(self, *, target_label, body, metadata=None):
        return SimpleNamespace(
            target_label=target_label,
            title="",
            body=body,
            metadata=metadata or {},
            mobile_relevance_status="strong",
        )

    def test_named_waterfall_mismatch_resolves_after_retargeting(self):
        submission = self._submission(
            target_label="Foster Falls",
            body="We loved Machine Falls in Tennessee.",
        )

        before = approved_quality(submission)
        self.assertTrue(before["needs_check"])
        self.assertEqual(before["kind"], "target_mismatch")
        self.assertEqual(before["suggested_target_label"], "Machine Falls")

        submission.target_label = "Machine Falls"
        after = approved_quality(submission)
        self.assertFalse(after["needs_check"])


class UGCTargetWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="target-workflow@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)
        self.queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id})
        self.discovery_url = reverse("ugc:discovery_searches", kwargs={"workspace_id": self.workspace.id})

        self.known_target = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.PHOTO,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            target_type="top_sight",
            target_id="machine-falls",
            target_label="Machine Falls",
            target_url="https://thetngame.com/top-sights/machine-falls/",
            title="Machine Falls",
            body="Machine Falls after the rain.",
            consent_confirmed=True,
        )
        self.mismatch = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            target_url="https://thetngame.com/top-sights/foster-falls/",
            title="Tennessee waterfall trip",
            body="We loved Machine Falls in Tennessee.",
            consent_confirmed=True,
        )

    def test_approved_picker_searches_full_catalog_and_marks_suggestion(self):
        review_url = reverse(
            "ugc:mobile_review",
            kwargs={"workspace_id": self.workspace.id, "submission_id": self.mismatch.id},
        )
        response = self.client.get(
            reverse("ugc:target_catalog", kwargs={"workspace_id": self.workspace.id}),
            {
                "submission": str(self.mismatch.id),
                "q": "Machine Falls",
                "back_to": f"{review_url}?tab=approved&draft_state=check",
                "return_to": f"{self.queue_url}?tab=approved&draft_state=check",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose Target")
        self.assertContains(response, "Machine Falls")
        self.assertContains(response, "Caption match")
        self.assertContains(response, "Use suggested target")

    def test_retarget_reruns_quality_check_and_records_audit_result(self):
        response = self.client.post(
            reverse(
                "ugc:mobile_retarget",
                kwargs={"workspace_id": self.workspace.id, "submission_id": self.mismatch.id},
            ),
            {
                "target_key": "top_sight::machine-falls",
                "return_to": f"{self.queue_url}?tab=approved&draft_state=check",
            },
        )

        self.assertRedirects(
            response,
            f"{self.queue_url}?tab=approved&draft_state=check",
            fetch_redirect_response=False,
        )
        self.mismatch.refresh_from_db()
        self.assertEqual(self.mismatch.target_id, "machine-falls")
        self.assertEqual(self.mismatch.target_label, "Machine Falls")
        self.assertFalse(approved_quality(self.mismatch)["needs_check"])

        audit = AuditEvent.objects.get(action="ugc.target_changed", target_id=str(self.mismatch.id))
        self.assertTrue(audit.metadata["quality_before"]["needs_check"])
        self.assertFalse(audit.metadata["quality_after"]["needs_check"])

    def test_retarget_rejects_protocol_relative_return_url(self):
        response = self.client.post(
            reverse(
                "ugc:mobile_retarget",
                kwargs={"workspace_id": self.workspace.id, "submission_id": self.mismatch.id},
            ),
            {
                "target_key": "top_sight::machine-falls",
                "return_to": "//evil.example/path?draft_state=check",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], self.queue_url)

    def test_pending_item_cannot_use_approved_retarget_route(self):
        self.mismatch.status = UGCSubmission.Status.PENDING
        self.mismatch.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse(
                "ugc:mobile_retarget",
                kwargs={"workspace_id": self.workspace.id, "submission_id": self.mismatch.id},
            ),
            {"target_key": "top_sight::machine-falls"},
        )

        self.assertEqual(response.status_code, 404)

    def test_new_discovery_search_continues_to_catalog_picker(self):
        response = self.client.post(
            reverse("ugc:save_discovery_search", kwargs={"workspace_id": self.workspace.id}),
            {
                "name": "Greeter Falls keyword",
                "platform": "instagram",
                "search_type": "keyword",
                "query": "Greeter Falls Tennessee",
                "cadence": "daily",
                "result_limit": "25",
            },
        )

        self.assertEqual(response.status_code, 302)
        location = response.headers["Location"]
        self.assertEqual(
            urlparse(location).path, reverse("ugc:target_catalog", kwargs={"workspace_id": self.workspace.id})
        )
        search_id = parse_qs(urlparse(location).query)["search_id"][0]
        self.workspace.refresh_from_db()
        saved = get_saved_search(self.workspace, search_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["target_id"], "")

    def test_discovery_search_picker_saves_canonical_catalog_target(self):
        search_id = "a1b2c3d4-1111-4222-8333-abcdefabcdef"
        self.workspace.discovery_searches = [
            {
                "id": search_id,
                "name": "Waterfall search",
                "platform": "instagram",
                "search_type": "keyword",
                "query": "Tennessee waterfalls",
                "cadence": "daily",
                "enabled": True,
            }
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])

        picker = self.client.get(
            reverse("ugc:target_catalog", kwargs={"workspace_id": self.workspace.id}),
            {"search_id": search_id, "return_to": self.discovery_url, "back_to": self.discovery_url},
        )
        self.assertEqual(picker.status_code, 200)
        self.assertContains(picker, "Default Target")
        self.assertContains(picker, "Use this target")
        self.assertContains(picker, "Target not listed? Add it manually")

        response = self.client.post(
            reverse(
                "ugc:update_discovery_search",
                kwargs={"workspace_id": self.workspace.id, "search_id": search_id},
            ),
            {
                "action": "set_target",
                "target_key": "top_sight::machine-falls",
                "target_label": "Forged label is ignored",
                "return_to": self.discovery_url,
            },
        )
        self.assertRedirects(response, self.discovery_url, fetch_redirect_response=False)

        self.workspace.refresh_from_db()
        saved = get_saved_search(self.workspace, search_id)
        self.assertEqual(saved["target_type"], "top_sight")
        self.assertEqual(saved["target_id"], "machine-falls")
        self.assertEqual(saved["target_label"], "Machine Falls")
        self.assertEqual(saved["target_url"], "https://thetngame.com/top-sights/machine-falls/")
        audit = AuditEvent.objects.get(action="ugc.discovery_search_target_changed", target_id=search_id)
        self.assertEqual(audit.metadata["selection_method"], "catalog")
        self.assertEqual(audit.metadata["to"]["target_label"], "Machine Falls")
