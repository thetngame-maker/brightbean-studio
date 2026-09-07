from datetime import timedelta
from unittest.mock import patch

from background_task.models import Task
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.ugc_discovery_providers import DiscoveryProviderError
from apps.common.ugc_discovery_search_views import _schedule_state, record_search_run
from apps.common.ugc_discovery_tasks import (
    DISCOVERY_PRIORITY,
    _claim_search,
    _next_keyword_depth,
    enqueue_saved_discovery_search,
    run_saved_discovery_search,
)
from apps.common.ugc_keyword_discovery import fetch_apify_keyword_results


class KeywordFailureTests(SimpleTestCase):
    def test_all_sources_failing_is_not_empty_success(self):
        with (
            patch(
                "apps.common.ugc_keyword_discovery._apify_sync",
                side_effect=DiscoveryProviderError("HTTP 400 minimum cost"),
            ) as provider,
            self.assertRaisesRegex(DiscoveryProviderError, "minimum cost"),
        ):
            fetch_apify_keyword_results({"query": "Foster Falls"})
        self.assertEqual(provider.call_count, 3)

    def test_error_dataset_is_not_empty_success(self):
        with (
            patch("apps.common.ugc_keyword_discovery._apify_sync", return_value=[{"error": "Scraper failed"}]),
            self.assertRaisesRegex(DiscoveryProviderError, "no usable Instagram posts"),
        ):
            fetch_apify_keyword_results({"query": "Foster Falls"})

    def test_empty_success_is_distinct_from_provider_failure(self):
        with patch("apps.common.ugc_keyword_discovery._apify_sync", return_value=[]):
            self.assertEqual(fetch_apify_keyword_results({"query": "Foster Falls"}), [])

    def test_fallback_can_recover_primary_failure(self):
        row = {
            "id": "123",
            "url": "https://www.instagram.com/p/ABC/",
            "ownerUsername": "hiker",
            "caption": "Foster Falls",
        }
        with patch(
            "apps.common.ugc_keyword_discovery._apify_sync",
            side_effect=[DiscoveryProviderError("primary unavailable"), [row], []],
        ):
            result = fetch_apify_keyword_results({"query": "Foster Falls"})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["discovery_provider_path"], "popular_reels")

    def test_partial_outage_with_no_results_is_not_success(self):
        with (
            patch(
                "apps.common.ugc_keyword_discovery._apify_sync",
                side_effect=[DiscoveryProviderError("primary unavailable"), [], []],
            ),
            self.assertRaises(DiscoveryProviderError),
        ):
            fetch_apify_keyword_results({"query": "Foster Falls"})


class DiscoveryQueueTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="discovery-check@example.com", password="testpass123", tos_accepted_at=timezone.now()
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.search_id = "12345678-1234-5678-1234-567812345678"
        self.previous_time = (timezone.now() - timedelta(days=8)).isoformat()
        self.workspace.discovery_searches = [
            {
                "id": self.search_id,
                "name": "Foster Falls",
                "query": "Foster Falls",
                "platform": "instagram",
                "search_type": "keyword",
                "enabled": True,
                "cadence": "weekly",
                "result_limit": 100,
                "target_type": "top_sight",
                "target_id": "foster-falls",
                "last_run_at": self.previous_time,
                "last_run_status": "success",
                "last_provider": "apify · depth200 · kw100/pr0/fb0",
                "last_created_count": 32,
                "last_duplicate_count": 65,
            }
        ]
        self.workspace.save(update_fields=["discovery_searches"])
        self.client.force_login(self.user)

    def current(self):
        self.workspace.refresh_from_db()
        return self.workspace.discovery_searches[0]

    def test_queue_preserves_results_and_scan_depth_and_deduplicates(self):
        self.assertTrue(enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True))
        self.assertFalse(enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True))
        item = self.current()
        self.assertEqual(item["last_created_count"], 32)
        self.assertEqual(item["last_run_at"], self.previous_time)
        self.assertEqual(_next_keyword_depth(item), 300)
        self.assertEqual(item["last_run_status"], "queued")
        self.assertFalse(_schedule_state(item)["due_now"])
        tasks = Task.objects.filter(task_name=run_saved_discovery_search.name)
        self.assertEqual(tasks.count(), 1)
        self.assertEqual(tasks.get().priority, DISCOVERY_PRIORITY)
        self.assertEqual(_claim_search(self.workspace.id, self.search_id)["last_run_status"], "running")

    def test_legacy_queue_is_reused_and_promoted(self):
        old = run_saved_discovery_search(str(self.workspace.id), self.search_id, False, True, schedule={"priority": 0})
        self.assertFalse(enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True))
        old.refresh_from_db()
        self.assertEqual(old.priority, DISCOVERY_PRIORITY)
        self.assertEqual(Task.objects.filter(task_name=old.task_name).count(), 1)

    def test_manual_search_only_runs_when_forced(self):
        self.workspace.discovery_searches[0]["cadence"] = "manual"
        self.workspace.save(update_fields=["discovery_searches"])
        self.assertFalse(enqueue_saved_discovery_search(self.workspace.id, self.search_id))
        self.assertTrue(enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True))
        self.assertIsNotNone(_claim_search(self.workspace.id, self.search_id, force=True))

    def test_running_search_cannot_be_requeued(self):
        _claim_search(self.workspace.id, self.search_id, force=True)
        self.assertFalse(enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True))

    def test_legacy_record_queue_preserves_previous_summary(self):
        record_search_run(self.workspace, self.search_id, status="queued", provider="apify")
        self.assertEqual(self.current()["last_created_count"], 32)
        self.assertEqual(_next_keyword_depth(self.current()), 300)

    def test_queue_page_and_status_match(self):
        enqueue_saved_discovery_search(self.workspace.id, self.search_id, force=True)
        response = self.client.get(reverse("ugc:discovery_searches", kwargs={"workspace_id": self.workspace.id}))
        self.assertContains(response, "Queued — waiting for worker")
        self.assertContains(response, "32 created")
        self.assertContains(response, "Sample import test")
        status = self.client.get(reverse("ugc:discovery_run_status", kwargs={"workspace_id": self.workspace.id})).json()
        self.assertEqual(status["queued_count"], 1)
        self.assertEqual(response.context["status_signature"], status["signature"])

    def test_no_live_provider_does_not_import_mock_data(self):
        with patch("apps.common.ugc_discovery_run_views.live_provider_ready", return_value=False):
            response = self.client.post(
                reverse(
                    "ugc:queue_background_test_run",
                    kwargs={"workspace_id": self.workspace.id, "search_id": self.search_id},
                )
            )
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Task.objects.filter(task_name=run_saved_discovery_search.name).exists())

    def test_failed_keyword_run_records_failure(self):
        with (
            patch("apps.common.ugc_discovery_tasks.configured_provider_name", return_value="apify"),
            patch(
                "apps.common.ugc_discovery_tasks.fetch_apify_keyword_results",
                side_effect=DiscoveryProviderError("Keyword provider failed"),
            ),
        ):
            run_saved_discovery_search.now(str(self.workspace.id), self.search_id, False, True)
        self.assertEqual(self.current()["last_run_status"], "failed")
        self.assertIn("Keyword provider failed", self.current()["last_run_error"])

    def test_empty_run_is_labeled_empty(self):
        with (
            patch("apps.common.ugc_discovery_tasks.configured_provider_name", return_value="apify"),
            patch("apps.common.ugc_discovery_tasks.fetch_apify_keyword_results", return_value=[]),
            patch("apps.common.ugc_discovery_tasks.repair_workspace_discovered_media"),
        ):
            run_saved_discovery_search.now(str(self.workspace.id), self.search_id, False, True)
        self.assertEqual(self.current()["last_run_status"], "empty")


class MediaRepairYieldTests(SimpleTestCase):
    def test_repairs_one_item_then_schedules_continuation(self):
        from types import SimpleNamespace

        from apps.common.ugc_approved_media_repair import repair_approved_media_batch

        item = SimpleNamespace(id="item", submitted_at=timezone.now())
        with (
            patch("apps.common.ugc_approved_media_repair.UGCSubmission") as model,
            patch("apps.common.ugc_approved_media_repair._is_instagram", return_value=True),
            patch("apps.common.ugc_approved_media_repair.repair_one_approved_submission") as repair,
            patch("apps.common.ugc_approved_media_repair.repair_approved_media_batch") as continuation,
        ):
            model.objects.for_workspace.return_value.select_related.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [
                item,
                item,
            ]
            repair_approved_media_batch.now("workspace")
        repair.assert_called_once_with(item)
        continuation.assert_called_once_with("workspace", item.submitted_at.isoformat(), "item", schedule=2)

    def test_scan_continues_past_non_instagram_items(self):
        from types import SimpleNamespace

        from apps.common.ugc_approved_media_repair import repair_approved_media_batch

        item = SimpleNamespace(id="item", submitted_at=timezone.now())
        with (
            patch("apps.common.ugc_approved_media_repair.UGCSubmission") as model,
            patch("apps.common.ugc_approved_media_repair._is_instagram", return_value=False),
            patch("apps.common.ugc_approved_media_repair.repair_one_approved_submission") as repair,
            patch("apps.common.ugc_approved_media_repair.repair_approved_media_batch") as continuation,
        ):
            model.objects.for_workspace.return_value.select_related.return_value.filter.return_value.order_by.return_value.__getitem__.return_value = [
                item
            ]
            repair_approved_media_batch.now("workspace")
        repair.assert_not_called()
        continuation.assert_called_once()
