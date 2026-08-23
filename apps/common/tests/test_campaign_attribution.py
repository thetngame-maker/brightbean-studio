import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.composer.models import Post
from apps.workspaces.models import Workspace

from ..campaign_attribution import (
    create_attribution_link,
    record_attribution_click,
    record_registration,
    verify_conversion_secret,
)
from ..models import (
    AuditEvent,
    CampaignAttributionClick,
    CampaignAttributionConversion,
    CampaignAttributionLink,
)
from ..tourism_impact import build_impact_snapshot


@override_settings(BB_TRUSTED_PROXIES=())
class CampaignAttributionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email="attribution@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.workspace.discovery_searches = [
            {
                "id": "foster-link-target",
                "name": "Foster Falls",
                "target_type": "top_sight",
                "target_id": "foster-falls",
                "target_label": "Foster Falls",
                "target_url": "https://thetngame.com/foster-falls/",
            },
            {
                "id": "greeter-link-target",
                "name": "Greeter Falls",
                "target_type": "top_sight",
                "target_id": "greeter-falls",
                "target_label": "Greeter Falls",
                "target_url": "https://thetngame.com/greeter-falls/",
            },
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Foster Falls summer campaign",
            caption="Visit Foster Falls this summer.",
        )
        self.client.force_login(self.user)

    def _link(self, *, secret="conversion-secret", **overrides):
        fields = {
            "workspace": self.workspace,
            "name": "Foster Falls Reel",
            "destination_url": "https://thetngame.com/foster-falls/?existing=1",
            "post": self.post,
            "target_type": "top_sight",
            "target_id": "foster-falls",
            "target_label": "Foster Falls",
            "target_url": "https://thetngame.com/foster-falls/",
            "utm_source": "instagram",
            "utm_medium": "organic_social",
            "utm_campaign": "foster-summer",
            "created_by": self.user,
            "updated_by": self.user,
        }
        fields.update(overrides)
        return create_attribution_link(conversion_secret=secret, **fields)

    def test_create_link_reuses_target_and_post_and_reveals_key_only_once(self):
        url = reverse("ugc:create_attribution_link", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            url,
            {
                "name": "Foster Falls Launch",
                "destination_url": "https://thetngame.com/foster-falls/",
                "target_key": "top_sight::foster-falls",
                "post_id": str(self.post.id),
                "utm_source": "instagram",
                "utm_medium": "organic",
                "utm_campaign": "foster-launch",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        link = CampaignAttributionLink.objects.get(name="Foster Falls Launch")
        self.assertEqual(link.target_id, "foster-falls")
        self.assertEqual(link.post, self.post)
        self.assertContains(response, "Copy this conversion key now")
        revealed_secret = response.context["attribution_reveal"]["secret"]
        self.assertTrue(verify_conversion_secret(link, revealed_secret))
        self.assertNotEqual(link.conversion_secret_hash, revealed_secret)
        self.assertNotIn(revealed_secret, str(AuditEvent.objects.get(action="campaign_attribution.link_created").metadata))

        second = self.client.get(
            reverse(
                "ugc:attribution_link_detail",
                kwargs={"workspace_id": self.workspace.id, "link_id": link.id},
            )
        )
        self.assertNotContains(second, revealed_secret)

    def test_list_is_server_rendered_and_paginates_twelve(self):
        for index in range(13):
            self._link(secret=f"secret-{index}", name=f"Campaign {index}", post=None)

        response = self.client.get(reverse("ugc:attribution_links", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["attribution_links"]), 12)
        self.assertEqual(response.context["attribution_page"].paginator.num_pages, 2)
        self.assertNotContains(response, "IntersectionObserver")

    def test_redirect_adds_tags_and_counts_repeat_visitor_once_per_day(self):
        link = self._link()
        url = reverse("attribution_public:redirect", kwargs={"code": link.code})
        request_meta = {"REMOTE_ADDR": "203.0.113.45", "HTTP_USER_AGENT": "Mobile Safari test"}

        first = Client().get(url, **request_meta)
        Client().get(url, **request_meta)

        self.assertEqual(first.status_code, 302)
        query = parse_qs(urlsplit(first["Location"]).query)
        self.assertEqual(query["existing"], ["1"])
        self.assertEqual(query["utm_source"], ["instagram"])
        self.assertEqual(query["utm_medium"], ["organic_social"])
        self.assertEqual(query["utm_campaign"], ["foster-summer"])
        self.assertEqual(query["tng_ref"], [link.code])
        self.assertEqual(first["Referrer-Policy"], "no-referrer")
        self.assertIn("no-store", first["Cache-Control"])
        link.refresh_from_db()
        self.assertEqual(link.click_count, 2)
        self.assertEqual(link.unique_visitor_count, 1)
        aggregate = CampaignAttributionClick.objects.get(link=link)
        self.assertEqual(aggregate.clicks, 2)
        self.assertNotIn("203.0.113.45", aggregate.visitor_hash)
        self.assertNotIn("Mobile Safari", aggregate.visitor_hash)

    def test_known_preview_bot_redirects_without_counting_and_inactive_link_is_gone(self):
        link = self._link()
        url = reverse("attribution_public:redirect", kwargs={"code": link.code})

        response = Client().get(
            url,
            REMOTE_ADDR="203.0.113.46",
            HTTP_USER_AGENT="facebookexternalhit/1.1",
        )
        self.assertEqual(response.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.click_count, 0)
        self.assertFalse(CampaignAttributionClick.objects.filter(link=link).exists())

        link.is_active = False
        link.save(update_fields=["is_active", "updated_at"])
        self.assertEqual(Client().get(url).status_code, 410)

    def test_webhook_auth_is_idempotent_and_retains_no_raw_event_or_personal_metadata(self):
        secret = "webhook-secret-value"
        link = self._link(secret=secret)
        url = reverse("attribution_public:conversion", kwargs={"code": link.code})
        raw_event_id = "registration-customer-very-sensitive-12345"
        payload = {
            "event_id": raw_event_id,
            "occurred_at": timezone.now().isoformat(),
            "metadata": {
                "registration_type": "player",
                "source": "TN Game",
                "email": "private@example.com",
                "name": "Private Player",
            },
        }

        invalid = Client().post(url, json.dumps(payload), content_type="application/json")
        created = Client().post(
            url,
            json.dumps(payload),
            content_type="application/json",
            HTTP_X_TN_ATTRIBUTION_KEY=secret,
        )
        duplicate = Client().post(
            url,
            json.dumps(payload),
            content_type="application/json",
            HTTP_X_TN_ATTRIBUTION_KEY=secret,
        )

        self.assertEqual(invalid.status_code, 401)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        conversion = CampaignAttributionConversion.objects.get(link=link)
        self.assertNotEqual(conversion.external_id_hash, raw_event_id)
        self.assertNotIn(raw_event_id, conversion.external_id_hash)
        self.assertNotIn(raw_event_id[-8:], conversion.external_id_hint)
        self.assertEqual(conversion.metadata, {"registration_type": "player", "source": "TN Game"})
        self.assertNotIn("private@example.com", str(conversion.metadata))
        link.refresh_from_db()
        self.assertEqual(link.registration_count, 1)

    def test_manual_registration_is_audited_and_cross_workspace_links_are_hidden(self):
        link = self._link()
        url = reverse(
            "ugc:record_attribution_registration",
            kwargs={"workspace_id": self.workspace.id, "link_id": link.id},
        )
        response = self.client.post(
            url,
            {"quantity": "7", "occurred_on": timezone.localdate().isoformat(), "note": "Weekly total"},
        )
        self.assertEqual(response.status_code, 302)
        link.refresh_from_db()
        self.assertEqual(link.registration_count, 7)
        self.assertTrue(AuditEvent.objects.filter(action="campaign_attribution.registration_recorded").exists())

        other_workspace = Workspace.objects.create(
            organization=self.workspace.organization,
            name="Other attribution workspace",
        )
        other_link = create_attribution_link(
            conversion_secret="other-secret",
            workspace=other_workspace,
            name="Private other campaign",
            destination_url="https://thetngame.com/private/",
        )
        hidden_url = reverse(
            "ugc:attribution_link_detail",
            kwargs={"workspace_id": self.workspace.id, "link_id": other_link.id},
        )
        self.assertEqual(self.client.get(hidden_url).status_code, 404)

    def test_conversion_key_rotation_invalidates_old_key_and_is_audited_without_secret(self):
        old_secret = "old-conversion-secret"
        link = self._link(secret=old_secret)
        url = reverse(
            "ugc:update_attribution_link",
            kwargs={"workspace_id": self.workspace.id, "link_id": link.id},
        )

        response = self.client.post(url, {"action": "rotate_key"}, follow=True)

        self.assertEqual(response.status_code, 200)
        new_secret = response.context["attribution_reveal"]["secret"]
        link.refresh_from_db()
        self.assertFalse(verify_conversion_secret(link, old_secret))
        self.assertTrue(verify_conversion_secret(link, new_secret))
        audit = AuditEvent.objects.get(action="campaign_attribution.conversion_key_rotated")
        self.assertNotIn(old_secret, str(audit.metadata))
        self.assertNotIn(new_secret, str(audit.metadata))

    def test_snapshot_scopes_first_party_outcomes_and_preserves_campaign_breakdown(self):
        foster = self._link()
        greeter = self._link(
            secret="greeter-secret",
            name="Greeter Falls Reel",
            post=None,
            target_id="greeter-falls",
            target_label="Greeter Falls",
        )
        now = timezone.now()
        record_attribution_click(
            foster,
            client_ip="203.0.113.50",
            user_agent="Safari",
            occurred_at=now,
        )
        record_attribution_click(
            foster,
            client_ip="203.0.113.51",
            user_agent="Safari",
            occurred_at=now,
        )
        record_registration(
            foster,
            external_id="registration-foster",
            occurred_at=now,
            source=CampaignAttributionConversion.Source.WEBHOOK,
            quantity=1,
        )
        record_attribution_click(
            greeter,
            client_ip="203.0.113.52",
            user_agent="Safari",
            occurred_at=now,
        )
        target = {
            "target_type": "top_sight",
            "target_id": "foster-falls",
            "target_label": "Foster Falls",
        }

        snapshot = build_impact_snapshot(
            self.workspace,
            period_start=timezone.localdate() - timedelta(days=7),
            period_end=timezone.localdate(),
            target=target,
        )

        self.assertEqual(snapshot["totals"]["tracked_link_clicks"], 2)
        self.assertEqual(snapshot["totals"]["tracked_website_visits"], 2)
        self.assertEqual(snapshot["totals"]["tracked_registrations"], 1)
        self.assertEqual(snapshot["totals"]["tracked_conversion_rate"], 50.0)
        self.assertEqual(len(snapshot["campaign_attribution"]), 1)
        self.assertEqual(snapshot["campaign_attribution"][0]["name"], "Foster Falls Reel")
        self.assertIn("no raw visitor identifiers", snapshot["methodology"])
