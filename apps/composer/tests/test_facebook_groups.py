import uuid
from types import SimpleNamespace

from django.http import HttpResponse
from django.test import SimpleTestCase
from django.utils import timezone

from apps.composer.facebook_groups import _inject_group_assistant, _serialize_post_target, normalize_group_url


class FacebookGroupUrlTests(SimpleTestCase):
    def test_normalizes_supported_facebook_group_urls(self):
        self.assertEqual(
            normalize_group_url("https://m.facebook.com/groups/12345/?ref=share"),
            "https://www.facebook.com/groups/12345/",
        )

    def test_rejects_non_group_or_non_facebook_urls(self):
        self.assertEqual(normalize_group_url("https://facebook.com/some-page"), "")
        self.assertEqual(normalize_group_url("https://example.com/groups/123"), "")
        self.assertEqual(normalize_group_url("javascript:alert(1)"), "")


class FacebookGroupComposerInjectionTests(SimpleTestCase):
    def test_post_target_serialization_includes_group_history_timestamp(self):
        posted_at = timezone.now()
        group_id = uuid.uuid4()
        link = SimpleNamespace(
            target=SimpleNamespace(
                id=group_id,
                name="Tennessee Waterfalls",
                url="https://www.facebook.com/groups/tennesseewaterfalls/",
            ),
            status="posted",
            posted_at=posted_at,
        )

        result = _serialize_post_target(link)

        self.assertEqual(result["id"], str(group_id))
        self.assertEqual(result["name"], "Tennessee Waterfalls")
        self.assertEqual(result["status"], "posted")
        self.assertEqual(result["posted_at"], posted_at.isoformat())

    def test_injects_group_assets_and_api_config(self):
        workspace_id = str(uuid.uuid4())
        post_id = str(uuid.uuid4())
        response = HttpResponse("<html><body>Composer</body></html>", content_type="text/html")

        result = _inject_group_assistant(response, workspace_id=workspace_id, post_id=post_id)
        html = result.content.decode("utf-8")

        self.assertIn("facebook_groups.css", html)
        self.assertIn("facebook_groups.js", html)
        self.assertIn(post_id, html)
        self.assertIn("facebook-groups", html)
        self.assertIn('id="tn-facebook-groups-script"', html)
        self.assertIn('data-catalog-url="/workspace/', html)
        self.assertNotIn("window.TN_FACEBOOK_GROUPS_API=", html)

    def test_does_not_inject_into_non_html_response(self):
        response = HttpResponse("{}", content_type="application/json")
        result = _inject_group_assistant(response, workspace_id=str(uuid.uuid4()))
        self.assertNotIn(b"facebook_groups.js", result.content)
