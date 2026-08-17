from unittest.mock import patch

from django.test import SimpleTestCase

from apps.common.ugc_discovery_providers import (
    DiscoveryProviderError,
    _normalize_apify_instagram_row,
    fetch_discovery_results,
    live_provider_ready,
    provider_health,
)


class UGCDiscoveryProviderTests(SimpleTestCase):
    def test_apify_requires_token_before_live_schedules_are_enabled(self):
        with patch.dict(
            "os.environ",
            {"UGC_DISCOVERY_PROVIDER": "apify"},
            clear=True,
        ):
            self.assertFalse(live_provider_ready())
            self.assertEqual(provider_health()["label"], "Apify needs API token")

    def test_apify_is_ready_when_provider_and_token_are_present(self):
        with patch.dict(
            "os.environ",
            {"UGC_DISCOVERY_PROVIDER": "apify", "APIFY_API_TOKEN": "test-token"},
            clear=True,
        ):
            self.assertTrue(live_provider_ready())
            self.assertEqual(provider_health()["provider"], "apify")

    def test_apify_instagram_row_normalizes_to_ingestion_shape(self):
        item = _normalize_apify_instagram_row(
            {
                "id": "123456",
                "shortCode": "ABC123",
                "url": "https://www.instagram.com/p/ABC123/",
                "caption": "Foster Falls after the rain",
                "displayUrl": "https://example.com/foster.jpg",
                "likesCount": 321,
                "commentsCount": 14,
                "videoPlayCount": 987,
                "ownerUsername": "dakota.meeks",
                "ownerFullName": "Dakota Meeks",
                "ownerId": "42",
            },
            {
                "query": "#fosterfalls",
                "name": "Tennessee Waterfalls",
                "target_label": "Foster Falls",
            },
        )

        self.assertEqual(item["platform"], "instagram")
        self.assertEqual(item["creator_handle"], "dakota.meeks")
        self.assertEqual(item["source_url"], "https://www.instagram.com/p/ABC123/")
        self.assertEqual(item["external_id"], "123456")
        self.assertEqual(item["title"], "Foster Falls")
        self.assertEqual(item["like_count"], 321)
        self.assertEqual(item["comment_count"], 14)
        self.assertEqual(item["view_count"], 987)

    def test_mock_provider_still_fails_closed_for_unattended_runs(self):
        with self.assertRaises(DiscoveryProviderError):
            fetch_discovery_results(
                {"id": "search-1", "platform": "instagram", "query": "#fosterfalls"},
                provider_name="mock",
                allow_mock=False,
            )
