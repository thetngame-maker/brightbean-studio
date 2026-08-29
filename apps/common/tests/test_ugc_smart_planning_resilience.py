import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.messages import get_messages
from django.core import signing
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User

from ..ugc_smart_planning_views import PLAN_SIGNING_SALT


class ApprovedSmartPlanResilienceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="smart-plan-resilience@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.client.force_login(self.user)
        self.url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})

    def _token(self, item_count=3):
        payload = {
            "workspace_id": str(self.workspace.id),
            "items": [
                {
                    "submission_id": str(uuid.uuid4()),
                    "account_id": str(uuid.uuid4()),
                    "scheduled_at": (timezone.now() + timedelta(days=index + 1)).isoformat(),
                    "reason": "resilience test",
                }
                for index in range(item_count)
            ],
        }
        return signing.dumps(payload, salt=PLAN_SIGNING_SALT, compress=True)

    @patch("apps.common.ugc_smart_planning_views.commit_smart_plan")
    def test_unexpected_batch_failure_isolated_without_500(self, mocked_commit):
        def commit_side_effect(workspace, payload, *, actor, caption_overrides=None):
            if len(payload["items"]) > 1:
                raise RuntimeError("simulated batch failure")
            return [object()], True

        mocked_commit.side_effect = commit_side_effect

        response = self.client.post(
            self.url,
            {"action": "commit", "plan_token": self._token(3)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("calendar:calendar", kwargs={"workspace_id": self.workspace.id}))
        self.assertEqual(mocked_commit.call_count, 4)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("scheduled 3 posts" in message for message in messages))
        self.assertTrue(any("safely skipped 0" in message for message in messages))

    @patch("apps.common.ugc_smart_planning_views.commit_smart_plan")
    def test_systemic_commit_failure_returns_safe_diagnostic(self, mocked_commit):
        mocked_commit.side_effect = RuntimeError("database unavailable")

        response = self.client.post(
            self.url,
            {"action": "commit", "plan_token": self._token(2)},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, self.url)
        self.assertEqual(mocked_commit.call_count, 3)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertTrue(any("could not schedule any posts" in message for message in messages))
        self.assertTrue(any("RuntimeError in the scheduling service" in message for message in messages))
        self.assertFalse(any("database unavailable" in message for message in messages))

    @patch("apps.common.ugc_smart_planning_views.commit_smart_plan")
    def test_actionable_value_error_is_surfaced_and_identifiers_are_redacted(self, mocked_commit):
        mocked_commit.side_effect = ValueError(
            "SocialAccount 123e4567-e89b-12d3-a456-426614174000 is invalid; reconnect before scheduling"
        )

        response = self.client.post(
            self.url,
            {"action": "commit", "plan_token": self._token(1)},
        )

        self.assertEqual(response.status_code, 302)
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        diagnostic = next(message for message in messages if "Diagnostic:" in message)
        self.assertIn("ValueError", diagnostic)
        self.assertIn("reconnect before scheduling", diagnostic)
        self.assertIn("[id]", diagnostic)
        self.assertNotIn("123e4567-e89b-12d3-a456-426614174000", diagnostic)
