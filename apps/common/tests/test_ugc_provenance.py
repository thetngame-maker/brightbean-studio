from django.test import SimpleTestCase

from apps.common.ugc_provenance import (
    build_provenance,
    get_provenance,
    provenance_label,
    set_provenance,
)


class UGCProvenanceTests(SimpleTestCase):
    def test_build_provenance_normalizes_provider_values(self):
        provenance = build_provenance(
            platform="Instagram",
            source_url=" https://www.instagram.com/p/example/ ",
            external_id="ABC123",
            creator_handle="@dakota.meeks",
            discovery_source="Apify Instagram",
            discovery_query="#tennesseewaterfalls",
        )

        self.assertEqual(provenance["platform"], "instagram")
        self.assertEqual(provenance["source_url"], "https://www.instagram.com/p/example/")
        self.assertEqual(provenance["external_id"], "ABC123")
        self.assertEqual(provenance["creator_handle"], "dakota.meeks")
        self.assertEqual(provenance["discovery_source"], "apify instagram")
        self.assertEqual(provenance["discovery_query"], "#tennesseewaterfalls")

    def test_set_provenance_preserves_other_metadata(self):
        metadata = set_provenance(
            {"studio_post_ids": ["post-1"]},
            build_provenance(platform="instagram", external_id="shortcode-1"),
        )

        self.assertEqual(metadata["studio_post_ids"], ["post-1"])
        self.assertEqual(metadata["provenance"]["platform"], "instagram")
        self.assertEqual(metadata["provenance"]["external_id"], "shortcode-1")

    def test_old_submission_metadata_gets_safe_direct_fallback(self):
        provenance = get_provenance({"studio_post_ids": []})

        self.assertEqual(provenance["platform"], "direct")
        self.assertEqual(provenance["source_url"], "")
        self.assertEqual(provenance_label({}, fallback_source="ui"), "Direct submission")

    def test_legacy_api_source_keeps_meaningful_fallback_label(self):
        self.assertEqual(provenance_label({}, fallback_source="api"), "API")
