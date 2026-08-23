from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..models import AuditEvent, UGCContentMission, UGCRightsPassport, UGCSubmission


class UGCContentMissionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="content-missions@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.workspace.discovery_searches = [
            {
                "id": "mission-target-search",
                "name": "Greeter Falls",
                "target_type": "top_sight",
                "target_id": "greeter-falls",
                "target_label": "Greeter Falls",
            }
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.client.force_login(self.user)
        self.list_url = reverse("ugc:content_missions", kwargs={"workspace_id": self.workspace.id})

    def create_submission(self, **overrides):
        values = {
            "workspace": self.workspace,
            "kind": UGCSubmission.Kind.COMMUNITY_POST,
            "status": UGCSubmission.Status.APPROVED,
            "source": UGCSubmission.Source.IMPORT,
            "contributor_handle": "tn_creator",
            "target_type": "top_sight",
            "target_id": "greeter-falls",
            "target_label": "Greeter Falls",
            "title": "Greeter Falls after the rain",
            "body": "Fresh spring conditions at Greeter Falls in Tennessee.",
            "consent_confirmed": True,
            "consent_at": timezone.now(),
            "metadata": {
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "granted"},
            },
        }
        values.update(overrides)
        return UGCSubmission.objects.create(**values)

    def test_create_launches_canonical_target_mission_and_audits(self):
        create_url = reverse("ugc:create_content_mission", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            create_url,
            {
                "title": "Fresh Greeter Falls Reels",
                "target_key": "top_sight::greeter-falls",
                "deliverables": "One vertical Reel with current waterfall and trail conditions",
                "offer": "Featured credit",
                "goal_count": "4",
                "action": "launch",
            },
        )

        mission = UGCContentMission.objects.get()
        self.assertRedirects(response, f"{self.list_url}?view=active", fetch_redirect_response=False)
        self.assertEqual(mission.status, UGCContentMission.Status.ACTIVE)
        self.assertEqual(mission.target_label, "Greeter Falls")
        self.assertEqual(mission.goal_count, 4)
        self.assertIn("feature it with credit", mission.creator_prompt)
        self.assertTrue(
            AuditEvent.objects.filter(action="ugc.content_mission_created", target_id=str(mission.id)).exists()
        )

    def test_invalid_second_target_system_cannot_be_created(self):
        create_url = reverse("ugc:create_content_mission", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            create_url,
            {
                "title": "Invented target",
                "target_key": "top_sight::not-in-the-catalog",
                "deliverables": "A Reel",
                "goal_count": "3",
                "action": "launch",
            },
        )

        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertFalse(UGCContentMission.objects.exists())

    def test_mission_progress_uses_stored_rights_quality_and_draft_data(self):
        mission = UGCContentMission.objects.create(
            workspace=self.workspace,
            title="Spring conditions",
            deliverables="Current conditions and a visitor tip",
            target_type="top_sight",
            target_id="greeter-falls",
            target_label="Greeter Falls",
            goal_count=2,
            status=UGCContentMission.Status.ACTIVE,
            starts_at=timezone.now() - timedelta(minutes=1),
            created_by=self.user,
        )
        ready = self.create_submission(
            metadata={
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "granted"},
                "studio_post_ids": ["draft-1"],
            }
        )
        passport = ready.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.save(update_fields=["status", "allow_organic_social", "updated_at"])
        self.create_submission(
            status=UGCSubmission.Status.PENDING,
            consent_confirmed=False,
            consent_at=None,
            contributor_handle="second_creator",
            metadata={
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "not_contacted"},
            },
        )

        response = self.client.get(self.list_url)
        rendered = response.context["missions"][0]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(rendered.id, mission.id)
        self.assertEqual(rendered.capture_count, 2)
        self.assertEqual(rendered.rights_count, 1)
        self.assertEqual(rendered.ready_count, 1)
        self.assertEqual(rendered.drafted_count, 1)
        self.assertEqual(rendered.progress_percentage, 50)
        self.assertContains(response, "Community Content")
        self.assertContains(response, "Ready content")

    def test_status_change_is_audited_and_list_is_paginated(self):
        missions = []
        for index in range(13):
            missions.append(
                UGCContentMission(
                    workspace=self.workspace,
                    title=f"Mission {index}",
                    deliverables="A current Reel",
                    target_type="top_sight",
                    target_id="greeter-falls",
                    target_label="Greeter Falls",
                    status=UGCContentMission.Status.ACTIVE,
                    created_by=self.user,
                )
            )
        UGCContentMission.objects.bulk_create(missions)
        mission = UGCContentMission.objects.order_by("created_at").first()
        update_url = reverse(
            "ugc:update_content_mission",
            kwargs={"workspace_id": self.workspace.id, "mission_id": mission.id},
        )

        response = self.client.post(update_url, {"action": "pause", "return_to": self.list_url})
        mission.refresh_from_db()
        self.assertRedirects(response, self.list_url, fetch_redirect_response=False)
        self.assertEqual(mission.status, UGCContentMission.Status.PAUSED)
        self.assertTrue(
            AuditEvent.objects.filter(action="ugc.content_mission_pause", target_id=str(mission.id)).exists()
        )

        page = self.client.get(self.list_url, {"view": "all"})
        self.assertEqual(len(page.context["missions"]), 12)
        self.assertEqual(page.context["mission_page"].paginator.num_pages, 2)
