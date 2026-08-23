from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.analytics.models import PostInsightsSnapshot
from apps.composer.models import PlatformPost, Post
from apps.members.models import WorkspaceMembership
from apps.notifications.models import DeliveryStatus, EventType, Notification, NotificationDelivery
from apps.social_accounts.models import SocialAccount
from apps.workspaces.models import Workspace

from ..models import (
    AuditEvent,
    ContentPerformanceProfile,
    TourismImpactReport,
    TourismImpactReportSchedule,
    UGCCreator,
    UGCSubmission,
)
from ..tourism_impact import build_impact_snapshot
from ..tourism_impact_schedules import completed_period, next_schedule_run
from ..tourism_impact_tasks import run_due_impact_report_schedules


class TourismImpactReportTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="impact@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.workspace.discovery_searches = [
            {
                "id": "impact-target",
                "name": "Foster Falls",
                "target_type": "top_sight",
                "target_id": "foster-falls",
                "target_label": "Foster Falls",
                "target_url": "https://thetngame.com/foster-falls/",
            },
            {
                "id": "impact-gap",
                "name": "Greeter Falls",
                "target_type": "top_sight",
                "target_id": "greeter-falls",
                "target_label": "Greeter Falls",
            },
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="impact-instagram",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.creator = UGCCreator.objects.create(workspace=self.workspace, display_name="Dakota Creator")
        self.submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            creator=self.creator,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            title="Foster Falls community Reel",
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            consent_confirmed=True,
            consent_at=timezone.now(),
            submitted_at=timezone.now() - timedelta(days=3),
            metadata={"permission": {"status": "granted"}},
        )
        passport = self.submission.rights_passport
        passport.status = passport.Status.GRANTED
        passport.allow_organic_social = True
        passport.granted_at = timezone.now() - timedelta(days=3)
        passport.save(update_fields=["status", "allow_organic_social", "granted_at", "updated_at"])
        self.post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title="Foster Falls weekend guide",
            caption="Community guide to Foster Falls.",
        )
        self.platform_post = PlatformPost.objects.create(
            post=self.post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHED,
            published_at=timezone.now() - timedelta(days=2),
            platform_post_id="impact-post",
        )
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=self.post,
            source_submission=self.submission,
            creator=self.creator,
            source_type=ContentPerformanceProfile.SourceType.UGC,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            created_by=self.user,
            updated_by=self.user,
        )
        for key, value in (
            ("reach", 10000),
            ("likes", 500),
            ("comments", 25),
            ("shares", 100),
            ("saves", 75),
            ("clicks", 40),
        ):
            PostInsightsSnapshot.objects.create(
                platform_post=self.platform_post,
                metric_key=key,
                date=timezone.localdate() - timedelta(days=1),
                value=value,
            )
        self.client.force_login(self.user)

    def _snapshot(self, target=None):
        return build_impact_snapshot(
            self.workspace,
            period_start=timezone.localdate() - timedelta(days=29),
            period_end=timezone.localdate(),
            target=target,
        )

    def _report(self, *, status=TourismImpactReport.Status.DRAFT):
        return TourismImpactReport.objects.create(
            workspace=self.workspace,
            title="Summer Foster Falls Impact",
            period_start=timezone.localdate() - timedelta(days=29),
            period_end=timezone.localdate(),
            status=status,
            snapshot=self._snapshot(),
            generated_by=self.user,
            shared_by=self.user if status == TourismImpactReport.Status.SHARED else None,
            shared_at=timezone.now() if status == TourismImpactReport.Status.SHARED else None,
        )

    def _partner_client(self):
        partner = User.objects.create_user(
            email="partner@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        WorkspaceMembership.objects.create(
            user=partner,
            workspace=self.workspace,
            workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
        )
        client = Client()
        client.force_login(partner)
        session = client.session
        session["is_portal_session"] = True
        session["portal_workspace_id"] = str(self.workspace.id)
        session.save()
        return partner, client

    def test_snapshot_combines_real_publishing_analytics_rights_and_coverage(self):
        snapshot = self._snapshot()

        self.assertEqual(snapshot["totals"]["published_posts"], 1)
        self.assertEqual(snapshot["totals"]["measured_exposure"], 10000)
        self.assertEqual(snapshot["totals"]["interactions"], 700)
        self.assertEqual(snapshot["totals"]["shares_saves"], 175)
        self.assertEqual(snapshot["totals"]["outbound_clicks"], 40)
        self.assertEqual(snapshot["totals"]["creator_participants"], 1)
        self.assertEqual(snapshot["totals"]["rights_cleared_assets"], 1)
        self.assertEqual(snapshot["totals"]["estimated_organic_value"], 120.0)
        self.assertEqual(snapshot["target_breakdown"][0]["target_label"], "Foster Falls")
        self.assertTrue(any(item["target_label"] == "Greeter Falls" for item in snapshot["coverage_gaps"]))

    def test_target_scope_uses_existing_target_identity(self):
        target = {
            "target_type": "top_sight",
            "target_id": "greeter-falls",
            "target_label": "Greeter Falls",
        }
        snapshot = self._snapshot(target)

        self.assertEqual(snapshot["totals"]["published_posts"], 0)
        self.assertEqual(snapshot["totals"]["destinations_total"], 1)
        self.assertEqual(snapshot["totals"]["destinations_covered"], 0)

    def test_create_report_is_a_stable_snapshot_and_audited(self):
        url = reverse("ugc:create_impact_report", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            url,
            {
                "title": "Partner Report",
                "period_start": (timezone.localdate() - timedelta(days=29)).isoformat(),
                "period_end": timezone.localdate().isoformat(),
                "target_key": "top_sight::foster-falls",
                "equivalent_cpm": "15.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        report = TourismImpactReport.objects.get(title="Partner Report")
        self.assertEqual(report.target_id, "foster-falls")
        self.assertEqual(report.snapshot["totals"]["estimated_organic_value"], 150.0)
        self.assertTrue(
            AuditEvent.objects.filter(action="tourism_impact.report_generated", target_id=str(report.id)).exists()
        )
        PostInsightsSnapshot.objects.filter(platform_post=self.platform_post, metric_key="reach").update(value=20000)
        report.refresh_from_db()
        self.assertEqual(report.snapshot["totals"]["measured_exposure"], 10000)

    def test_list_is_server_rendered_and_paginates_twelve(self):
        for index in range(13):
            report = self._report()
            report.title = f"Report {index}"
            report.save(update_fields=["title", "updated_at"])
        response = self.client.get(reverse("ugc:impact_reports", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Impact Reports")
        self.assertEqual(len(response.context["impact_reports"]), 12)
        self.assertEqual(response.context["impact_page"].paginator.num_pages, 2)
        self.assertNotContains(response, "IntersectionObserver")

    def test_share_adds_only_shared_report_to_secure_partner_portal(self):
        partner, partner_client = self._partner_client()
        draft = self._report()
        draft.title = "Internal draft report"
        draft.save(update_fields=["title", "updated_at"])
        shared = self._report(status=TourismImpactReport.Status.SHARED)
        shared.title = "Shared partner report"
        shared.save(update_fields=["title", "updated_at"])
        portal_url = reverse("client_portal:reports")

        response = partner_client.get(portal_url)
        self.assertContains(response, shared.title)
        self.assertNotContains(response, draft.title)

        detail_url = reverse("client_portal:report_detail", kwargs={"report_id": shared.id})
        self.assertEqual(partner_client.get(detail_url).status_code, 200)
        hidden_url = reverse("client_portal:report_detail", kwargs={"report_id": draft.id})
        self.assertEqual(partner_client.get(hidden_url).status_code, 404)

        share_url = reverse(
            "ugc:update_impact_report",
            kwargs={"workspace_id": self.workspace.id, "report_id": draft.id},
        )
        self.client.post(share_url, {"action": "share"})
        draft.refresh_from_db()
        self.assertEqual(draft.status, TourismImpactReport.Status.SHARED)
        self.assertTrue(Notification.objects.filter(user=partner, event_type=EventType.REPORT_GENERATED).exists())
        self.assertTrue(AuditEvent.objects.filter(action="tourism_impact.report_shared").exists())

    def test_partner_csv_export_cannot_access_internal_draft(self):
        _partner, partner_client = self._partner_client()
        draft = self._report()
        shared = self._report(status=TourismImpactReport.Status.SHARED)
        shared.title = '=HYPERLINK("https://example.com","Report")'
        shared.save(update_fields=["title", "updated_at"])

        draft_url = reverse("client_portal:report_csv", kwargs={"report_id": draft.id})
        shared_url = reverse("client_portal:report_csv", kwargs={"report_id": shared.id})
        self.assertEqual(partner_client.get(draft_url).status_code, 404)
        response = partner_client.get(shared_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn(b"Measured exposure", response.content)
        self.assertIn(b"'=HYPERLINK", response.content)
        self.assertTrue(
            AuditEvent.objects.filter(action="tourism_impact.partner_exported", target_id=str(shared.id)).exists()
        )

    def test_partner_report_detail_is_workspace_scoped(self):
        _partner, partner_client = self._partner_client()
        other_workspace = Workspace.objects.create(
            organization=self.workspace.organization,
            name="Other Tourism Workspace",
        )
        other_report = TourismImpactReport.objects.create(
            workspace=other_workspace,
            title="Private report from another workspace",
            period_start=timezone.localdate() - timedelta(days=7),
            period_end=timezone.localdate(),
            status=TourismImpactReport.Status.SHARED,
            snapshot={"totals": {}},
        )

        url = reverse("client_portal:report_detail", kwargs={"report_id": other_report.id})
        self.assertEqual(partner_client.get(url).status_code, 404)

    def test_manual_partner_outcomes_are_labeled_and_conversion_is_calculated(self):
        report = self._report()
        update_url = reverse(
            "ugc:update_impact_report",
            kwargs={"workspace_id": self.workspace.id, "report_id": report.id},
        )
        self.client.post(
            update_url,
            {
                "action": "save",
                "title": report.title,
                "website_visits": "400",
                "registrations": "20",
                "partner_notes": "Registrations supplied from TN Game reporting.",
            },
        )
        detail_url = reverse(
            "ugc:impact_report_detail",
            kwargs={"workspace_id": self.workspace.id, "report_id": report.id},
        )
        response = self.client.get(detail_url)

        self.assertContains(response, "WEBSITE VISITS — PARTNER SUPPLIED")
        self.assertContains(response, "5.0%")
        self.assertContains(response, "Registrations supplied from TN Game reporting.")

    def test_completed_calendar_periods_and_next_run_respect_workspace_time(self):
        self.assertEqual(
            completed_period(TourismImpactReportSchedule.Cadence.WEEKLY, as_of=date(2026, 8, 23)),
            (date(2026, 8, 10), date(2026, 8, 16)),
        )
        self.assertEqual(
            completed_period(TourismImpactReportSchedule.Cadence.MONTHLY, as_of=date(2026, 8, 23)),
            (date(2026, 7, 1), date(2026, 7, 31)),
        )
        self.assertEqual(
            completed_period(TourismImpactReportSchedule.Cadence.QUARTERLY, as_of=date(2026, 8, 23)),
            (date(2026, 4, 1), date(2026, 6, 30)),
        )
        self.workspace.timezone = "America/Chicago"
        self.workspace.save(update_fields=["timezone", "updated_at"])
        run_at = next_schedule_run(
            TourismImpactReportSchedule.Cadence.MONTHLY,
            self.workspace,
            after=datetime(2026, 8, 23, 12, tzinfo=ZoneInfo("UTC")),
        )
        self.assertEqual(run_at, datetime(2026, 9, 1, 13, tzinfo=ZoneInfo("UTC")))

    def test_recurring_schedule_uses_existing_target_and_is_audited(self):
        url = reverse("ugc:create_impact_report_schedule", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            url,
            {
                "name": "Foster Falls Monthly Partner Update",
                "cadence": "monthly",
                "delivery_mode": "share",
                "target_key": "top_sight::foster-falls",
                "equivalent_cpm": "14.50",
            },
        )

        self.assertEqual(response.status_code, 302)
        schedule = TourismImpactReportSchedule.objects.get(name="Foster Falls Monthly Partner Update")
        self.assertEqual(schedule.target_id, "foster-falls")
        self.assertTrue(schedule.auto_share)
        self.assertTrue(schedule.is_active)
        self.assertGreater(schedule.next_run_at, timezone.now())
        self.assertTrue(
            AuditEvent.objects.filter(action="tourism_impact.schedule_created", target_id=str(schedule.id)).exists()
        )
        page = self.client.get(reverse("ugc:impact_reports", kwargs={"workspace_id": self.workspace.id}))
        self.assertContains(page, "Automate partner reports")
        self.assertContains(page, schedule.name)
        self.assertNotContains(page, "IntersectionObserver")

    def test_run_now_is_idempotent_and_delivers_through_secure_portal_preferences(self):
        partner, partner_client = self._partner_client()
        schedule = TourismImpactReportSchedule.objects.create(
            workspace=self.workspace,
            name="Monthly Partner Impact",
            cadence=TourismImpactReportSchedule.Cadence.MONTHLY,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            equivalent_cpm="12.00",
            auto_share=True,
            next_run_at=timezone.now() + timedelta(days=5),
            created_by=self.user,
            updated_by=self.user,
        )
        url = reverse(
            "ugc:update_impact_report_schedule",
            kwargs={"workspace_id": self.workspace.id, "schedule_id": schedule.id},
        )
        first = self.client.post(url, {"action": "run"})

        self.assertEqual(first.status_code, 302)
        report = TourismImpactReport.objects.get(source_schedule=schedule)
        self.assertEqual(report.status, TourismImpactReport.Status.SHARED)
        self.assertEqual(report.target_id, "foster-falls")
        self.assertIsNotNone(report.shared_at)
        self.assertTrue(
            Notification.objects.filter(
                user=partner,
                event_type=EventType.REPORT_GENERATED,
                data__report_id=str(report.id),
            ).exists()
        )
        self.assertTrue(
            NotificationDelivery.objects.filter(
                notification__user=partner,
                notification__data__report_id=str(report.id),
                status=DeliveryStatus.DELIVERED,
            ).exists()
        )
        self.assertContains(partner_client.get(reverse("client_portal:reports")), report.title)
        second = self.client.post(url, {"action": "run"})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(TourismImpactReport.objects.filter(source_schedule=schedule).count(), 1)
        self.assertEqual(
            Notification.objects.filter(user=partner, data__report_id=str(report.id)).count(),
            1,
        )
        self.assertTrue(AuditEvent.objects.filter(action="tourism_impact.scheduled_report_shared").exists())

    def test_due_worker_generates_internal_draft_but_skips_paused_schedule(self):
        due = TourismImpactReportSchedule.objects.create(
            workspace=self.workspace,
            name="Weekly Review Draft",
            cadence=TourismImpactReportSchedule.Cadence.WEEKLY,
            auto_share=False,
            next_run_at=timezone.now() - timedelta(minutes=1),
            created_by=self.user,
        )
        paused = TourismImpactReportSchedule.objects.create(
            workspace=self.workspace,
            name="Paused Monthly Report",
            cadence=TourismImpactReportSchedule.Cadence.MONTHLY,
            auto_share=True,
            is_active=False,
            next_run_at=timezone.now() - timedelta(minutes=1),
            created_by=self.user,
        )

        run_due_impact_report_schedules.now()

        report = TourismImpactReport.objects.get(source_schedule=due)
        self.assertEqual(report.status, TourismImpactReport.Status.DRAFT)
        self.assertFalse(TourismImpactReport.objects.filter(source_schedule=paused).exists())
        due.refresh_from_db()
        self.assertGreater(due.next_run_at, timezone.now())

    def test_schedule_actions_are_workspace_scoped_and_archive_preserves_reports(self):
        other_workspace = Workspace.objects.create(
            organization=self.workspace.organization,
            name="Other Schedule Workspace",
        )
        schedule = TourismImpactReportSchedule.objects.create(
            workspace=other_workspace,
            name="Private Other Workspace Schedule",
            cadence=TourismImpactReportSchedule.Cadence.MONTHLY,
            next_run_at=timezone.now() + timedelta(days=3),
        )
        wrong_url = reverse(
            "ugc:update_impact_report_schedule",
            kwargs={"workspace_id": self.workspace.id, "schedule_id": schedule.id},
        )
        self.assertEqual(self.client.post(wrong_url, {"action": "pause"}).status_code, 404)

        own = TourismImpactReportSchedule.objects.create(
            workspace=self.workspace,
            name="Archive Me",
            cadence=TourismImpactReportSchedule.Cadence.MONTHLY,
            next_run_at=timezone.now() + timedelta(days=3),
        )
        own_url = reverse(
            "ugc:update_impact_report_schedule",
            kwargs={"workspace_id": self.workspace.id, "schedule_id": own.id},
        )
        self.client.post(own_url, {"action": "run"})
        self.client.post(own_url, {"action": "archive"})
        own.refresh_from_db()
        self.assertFalse(own.is_active)
        self.assertIsNotNone(own.archived_at)
        self.assertTrue(TourismImpactReport.objects.filter(source_schedule=own).exists())
        self.assertTrue(AuditEvent.objects.filter(action="tourism_impact.schedule_archived").exists())
