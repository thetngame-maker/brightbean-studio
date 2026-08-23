from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.common.models import AuditEvent, UGCCreator, UGCCreatorTask, UGCSubmission
from apps.inbox.models import InboxMessage
from apps.social_accounts.models import SocialAccount

from ..command_center import build_command_center
from ..models import PlatformPost, Post


class FiveMinuteCommandCenterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="command-center@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="command-instagram",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def _platform_post(self, status, *, title, scheduled_at=None, published_at=None, error=""):
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title=title,
            caption=f"Caption for {title}",
        )
        return PlatformPost.objects.create(
            post=post,
            social_account=self.account,
            status=status,
            scheduled_at=scheduled_at,
            published_at=published_at,
            publish_error=error,
        )

    def _creator_task(self):
        creator = UGCCreator.objects.create(
            workspace=self.workspace,
            display_name="Waterfall Creator",
        )
        return UGCCreatorTask.objects.create(
            workspace=self.workspace,
            creator=creator,
            kind=UGCCreatorTask.Kind.FOLLOW_UP,
            title="Send creator follow-up",
            due_at=timezone.now() - timedelta(hours=1),
            created_by=self.user,
        )

    def test_command_center_combines_real_queues_into_a_five_minute_run(self):
        self._platform_post(
            PlatformPost.Status.FAILED,
            title="Failed waterfall Reel",
            error="Instagram rejected the upload",
        )
        self._platform_post(PlatformPost.Status.PENDING_REVIEW, title="Review this trail post")
        self._creator_task()
        InboxMessage.objects.create(
            workspace=self.workspace,
            social_account=self.account,
            platform_message_id="command-message",
            message_type=InboxMessage.MessageType.DM,
            sender_name="Tennessee Traveler",
            body="Is Foster Falls open?",
            status=InboxMessage.Status.UNREAD,
            received_at=timezone.now(),
        )
        response = self.client.get(
            reverse("composer:command_center", kwargs={"workspace_id": self.workspace.id}),
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["X-Studio-Command-Center"], "1")
        self.assertContains(response, "Your five-minute run is ready")
        self.assertContains(response, "Failed waterfall Reel")
        self.assertContains(response, "Review this trail post")
        briefing = response.context["command_center"]
        self.assertLessEqual(briefing["run_minutes"], 5)
        self.assertEqual(briefing["run"][0]["kind"], "Publishing")
        self.assertEqual(briefing["counts"]["inbox_unread"], 1)

    def test_community_today_reuses_mobile_followup_and_prospect_logic(self):
        UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.PENDING,
            source=UGCSubmission.Source.IMPORT,
            title="Foster Falls Reel",
            body="A beautiful day at Foster Falls Tennessee",
            target_label="Foster Falls",
            contributor_handle="waterfallcreator",
            metadata={
                "provenance": {
                    "platform": "instagram",
                    "discovery_source": "saved_search",
                    "discovery_query": "Foster Falls",
                },
                "discovery_import": {"media_type": "video", "like_count": 500, "comment_count": 25},
                "permission": {"status": "not_contacted"},
            },
        )

        briefing = build_command_center(
            self.workspace,
            permissions={"approve_posts": True, "use_inbox": True},
        )

        self.assertEqual(briefing["counts"]["community_today"], 1)
        community_action = next(action for action in briefing["actions"] if action["kind"] == "Community")
        self.assertIn("permission=today", community_action["url"])
        self.assertIn("strong Reel prospect", community_action["detail"])

    def test_today_momentum_uses_workspace_day_and_existing_schedule_fields(self):
        now = timezone.now()
        self._platform_post(
            PlatformPost.Status.SCHEDULED,
            title="Scheduled today",
            scheduled_at=now + timedelta(hours=1),
        )
        self._platform_post(
            PlatformPost.Status.PUBLISHED,
            title="Published today",
            published_at=now,
        )

        briefing = build_command_center(self.workspace, permissions={})

        self.assertEqual(briefing["counts"]["scheduled_today"], 1)
        self.assertEqual(briefing["counts"]["published_today"], 1)

    def test_creator_task_can_be_completed_from_command_center_with_existing_audit_flow(self):
        task = self._creator_task()
        command_url = reverse("composer:command_center", kwargs={"workspace_id": self.workspace.id})
        update_url = reverse(
            "ugc:update_creator_task",
            kwargs={"workspace_id": self.workspace.id, "task_id": task.id},
        )

        response = self.client.post(update_url, {"action": "complete", "return_to": command_url})

        task.refresh_from_db()
        self.assertRedirects(response, command_url, fetch_redirect_response=False)
        self.assertEqual(task.status, UGCCreatorTask.Status.DONE)
        self.assertTrue(
            AuditEvent.objects.filter(action="ugc.creator_task_complete", metadata__task_id=str(task.id)).exists()
        )

    def test_empty_command_center_has_no_side_effects(self):
        before_posts = Post.objects.count()

        response = self.client.get(reverse("composer:command_center", kwargs={"workspace_id": self.workspace.id}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Nothing urgent is waiting")
        self.assertEqual(Post.objects.count(), before_posts)
        self.assertEqual(AuditEvent.objects.count(), 0)
