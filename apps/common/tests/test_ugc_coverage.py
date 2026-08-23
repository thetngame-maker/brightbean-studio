from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import UGCSubmission
from ..ugc_coverage import build_coverage_map


class UGCCoverageMapTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="coverage-map@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)

    def create_submission(self, target_id, target_label, **overrides):
        values = {
            "workspace": self.workspace,
            "kind": UGCSubmission.Kind.COMMUNITY_POST,
            "status": UGCSubmission.Status.APPROVED,
            "source": UGCSubmission.Source.IMPORT,
            "contributor_handle": f"creator_{target_id}",
            "target_type": "top_sight",
            "target_id": target_id,
            "target_label": target_label,
            "title": f"{target_label} Reel",
            "body": f"A beautiful day at {target_label} in Tennessee.",
            "consent_confirmed": True,
            "consent_at": timezone.now(),
            "metadata": {
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "granted"},
                "discovery_import": {"like_count": 100, "comment_count": 10, "view_count": 1000},
            },
        }
        values.update(overrides)
        return UGCSubmission.objects.create(**values)

    def test_coverage_classifies_gaps_permission_thin_stale_and_strong(self):
        self.workspace.discovery_searches = [
            {
                "id": "gap-search",
                "name": "Greeter Falls",
                "target_type": "top_sight",
                "target_id": "greeter-falls",
                "target_label": "Greeter Falls",
                "resolved_location_lat": 35.436,
                "resolved_location_lng": -85.696,
            }
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.create_submission(
            "foster-falls",
            "Foster Falls",
            contributor_handle="permission_gap",
            consent_confirmed=False,
            consent_at=None,
            metadata={
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "not_contacted"},
            },
        )
        self.create_submission("machine-falls", "Machine Falls", contributor_handle="thin_coverage")
        stale = self.create_submission("ruby-falls", "Ruby Falls", contributor_handle="stale_coverage")
        UGCSubmission.objects.filter(id=stale.id).update(submitted_at=timezone.now() - timedelta(days=120))
        self.create_submission("rock-island", "Rock Island", contributor_handle="strong_one")
        self.create_submission(
            "rock-island",
            "Rock Island",
            contributor_handle="strong_two",
            metadata={
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "granted"},
                "studio_post_ids": ["draft-1"],
            },
        )

        coverage = build_coverage_map(self.workspace)
        by_id = {item["target_id"]: item for item in coverage["targets"]}

        self.assertEqual(by_id["greeter-falls"]["coverage_state"], "gap")
        self.assertEqual(by_id["foster-falls"]["coverage_state"], "permission")
        self.assertEqual(by_id["machine-falls"]["coverage_state"], "thin")
        self.assertEqual(by_id["ruby-falls"]["coverage_state"], "stale")
        self.assertEqual(by_id["rock-island"]["coverage_state"], "strong")
        self.assertEqual(by_id["rock-island"]["publishable_count"], 2)
        self.assertEqual(by_id["rock-island"]["drafted_count"], 1)
        self.assertGreater(by_id["rock-island"]["engagement_score"], 0)
        self.assertIsNotNone(by_id["greeter-falls"]["map_x"])
        self.assertEqual(coverage["mapped_count"], 1)

    def test_coverage_view_is_server_rendered_filtered_and_paginated(self):
        searches = []
        for index in range(13):
            searches.append(
                {
                    "id": f"gap-{index}",
                    "name": f"Coverage Gap {index}",
                    "target_type": "top_sight",
                    "target_id": f"coverage-gap-{index}",
                    "target_label": f"Coverage Gap {index}",
                }
            )
        self.workspace.discovery_searches = searches
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        url = reverse("ugc:coverage_map", kwargs={"workspace_id": self.workspace.id})

        response = self.client.get(url, {"view": "gap"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Coverage Map")
        self.assertContains(response, "No coverage")
        self.assertEqual(len(response.context["coverage_targets"]), 12)
        self.assertEqual(response.context["coverage_page"].paginator.num_pages, 2)

        searched = self.client.get(url, {"q": "Gap 12"})
        self.assertEqual(searched.status_code, 200)
        self.assertContains(searched, "Coverage Gap 12")
        self.assertNotContains(searched, "Coverage Gap 11")
