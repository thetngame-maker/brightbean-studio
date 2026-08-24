from types import SimpleNamespace

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import AuditEvent, UGCSubmission
from ..ugc_smart_selection import normalize_smart_rules, smart_selection_for, workspace_smart_rules


class SmartSelectionRulesTests(SimpleTestCase):
    def submission(self, body, *, title="Waterfall reel", metadata=None):
        return SimpleNamespace(
            title=title,
            body=body,
            target_label="",
            metadata=metadata or {},
        )

    def rules(self):
        return {"grant": ["Tennessee", "TN", "Foster Falls"], "remove": ["NY", "PNW", "New York"]}

    def test_tennessee_keyword_suggests_grant(self):
        result = smart_selection_for(self.submission("A perfect weekend in Tennessee."), self.rules())

        self.assertEqual(result["decision"], "grant")
        self.assertEqual(result["grant_matches"], ["Tennessee"])

    def test_out_of_state_keyword_suggests_remove(self):
        result = smart_selection_for(self.submission("Four favorite waterfalls in the PNW."), self.rules())

        self.assertEqual(result["decision"], "remove")
        self.assertEqual(result["remove_matches"], ["PNW"])

    def test_conflicting_locations_are_left_for_manual_review(self):
        result = smart_selection_for(self.submission("Tennessee creator visiting New York."), self.rules())

        self.assertEqual(result["decision"], "review")
        self.assertIn("Conflicting", result["reason"])

    def test_short_tn_keyword_uses_word_boundaries(self):
        result = smart_selection_for(self.submission("A mountain waterfall adventure."), self.rules())

        self.assertEqual(result["decision"], "review")

    def test_discovery_location_is_included(self):
        submission = self.submission(
            "Four favorite waterfalls.",
            metadata={"discovery_import": {"location_name": "Foster Falls, TN"}},
        )

        self.assertEqual(smart_selection_for(submission, self.rules())["decision"], "grant")

    def test_rule_normalization_deduplicates_case_insensitively(self):
        rules = normalize_smart_rules(["Tennessee", " tennessee ", "TN"], ["PNW", "pnw"])

        self.assertEqual(rules, {"grant": ["Tennessee", "TN"], "remove": ["PNW"]})


class SmartSelectionViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="smart-selection@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)

    def test_workspace_keywords_can_be_saved_and_are_audited(self):
        queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            reverse("ugc:mobile_smart_rules", kwargs={"workspace_id": self.workspace.id}),
            {
                "grant_keywords": "Tennessee\nTN\nSouth Cumberland",
                "remove_keywords": "NY, PNW, California",
                "return_to": f"{queue_url}?tab=discovered",
            },
        )

        self.assertRedirects(response, f"{queue_url}?tab=discovered", fetch_redirect_response=False)
        self.workspace.refresh_from_db()
        self.assertEqual(
            self.workspace.community_smart_rules,
            {
                "grant": ["Tennessee", "TN", "South Cumberland"],
                "remove": ["NY", "PNW", "California"],
            },
        )
        audit = AuditEvent.objects.get(action="ugc.smart_selection_rules_updated")
        self.assertEqual(audit.metadata["grant_keyword_count"], 3)
        self.assertEqual(audit.metadata["remove_keyword_count"], 3)

    def test_mobile_discovered_queue_renders_review_first_suggestions(self):
        UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.PENDING,
            source=UGCSubmission.Source.IMPORT,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            title="Tennessee waterfall reel",
            body="A beautiful day in Tennessee.",
            metadata={
                "provenance": {
                    "platform": "instagram",
                    "discovery_source": "saved_search",
                    "source_url": "https://www.instagram.com/p/example/",
                },
                "permission": {"status": "not_contacted"},
            },
        )
        response = self.client.get(
            reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id}),
            {"tab": "discovered", "relevance": "all"},
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Smart AI Grant")
        self.assertContains(response, "Smart AI Remove")
        self.assertContains(response, 'data-smart-decision="grant"')
        self.assertContains(response, "Smart AI only preselects posts")
        self.assertEqual(workspace_smart_rules(self.workspace)["grant"][0], "Tennessee")
