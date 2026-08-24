from datetime import time, timedelta
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.analytics.models import PostInsightsSnapshot
from apps.calendar.models import PostingSlot
from apps.composer.models import PlatformPost, Post
from apps.media_library.models import MediaAsset
from apps.social_accounts.models import SocialAccount

from ..models import AuditEvent, ContentPerformanceProfile, UGCRightsPassport, UGCSubmission
from ..ugc_smart_planning import build_smart_plan


class ApprovedSmartPlanningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="approved-smart-plan@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.waterfalls = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="smart-waterfalls",
            account_name="TN Waterfalls",
            account_handle="tnwaterfalls",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.general = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="facebook",
            account_platform_id="smart-general",
            account_name="The TN Game",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        for account in (self.waterfalls, self.general):
            for day in (0, 2, 4):
                PostingSlot.objects.create(social_account=account, day_of_week=day, time=time(10, 0))
        self.client.force_login(self.user)

    def create_submission(
        self,
        *,
        title,
        target,
        likes,
        comments=0,
        views=0,
        body="A beautiful Tennessee adventure.",
        handle="tncreator",
        metadata_extra=None,
        allowed_account_ids=None,
    ):
        asset = MediaAsset.objects.create(
            organization=self.workspace.organization,
            workspace=self.workspace,
            uploaded_by=self.user,
            file=ContentFile(b"image-bytes", name=f"{title.lower().replace(' ', '-')}.jpg"),
            filename=f"{title}.jpg",
            media_type=MediaAsset.MediaType.IMAGE,
            mime_type="image/jpeg",
            file_size=11,
        )
        metadata = {
            "permission": {"status": "granted"},
            "provenance": {
                "platform": "instagram",
                "discovery_source": "saved_search",
                "source_url": "https://www.instagram.com/p/smart-plan/",
            },
            "discovery_import": {
                "like_count": likes,
                "comment_count": comments,
                "view_count": views,
                "media_type": "image",
            },
            **(metadata_extra or {}),
        }
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            contributor_handle=handle,
            target_type="top_sight",
            target_id=target.lower().replace(" ", "-"),
            target_label=target,
            title=title,
            body=body,
            media_asset=asset,
            consent_confirmed=True,
            consent_version="creator-rights-portal-v1",
            consent_at=timezone.now(),
            metadata=metadata,
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.credit_required = True
        passport.credit_text = f"@{handle}"
        passport.allowed_account_ids = allowed_account_ids or []
        passport.save(
            update_fields=[
                "status",
                "allow_organic_social",
                "credit_required",
                "credit_text",
                "allowed_account_ids",
                "updated_at",
            ]
        )
        return submission

    def create_published_history(self, account, *, likes, days_ago, target="Foster Falls"):
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title=f"History {days_ago}",
            caption="Recent account post",
        )
        platform_post = PlatformPost.objects.create(
            post=post,
            social_account=account,
            status=PlatformPost.Status.PUBLISHED,
            platform_post_id=f"history-{account.id}-{days_ago}",
            published_at=timezone.now() - timedelta(days=days_ago),
        )
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=post,
            source_type=ContentPerformanceProfile.SourceType.BRANDED,
            target_type="top_sight",
            target_id=target.lower().replace(" ", "-"),
            target_label=target,
            created_by=self.user,
        )
        PostInsightsSnapshot.objects.create(
            platform_post=platform_post,
            metric_key="reach",
            date=timezone.localdate(),
            value=1000,
        )
        PostInsightsSnapshot.objects.create(
            platform_post=platform_post,
            metric_key="likes",
            date=timezone.localdate(),
            value=likes,
        )
        return platform_post

    def test_preview_ranks_engagement_learns_history_and_fits_specialized_account(self):
        strongest = self.create_submission(
            title="Viral Foster Falls",
            target="Foster Falls",
            likes=5000,
            comments=200,
            views=100000,
            body="Foster Falls is roaring after the rain.",
        )
        self.create_submission(title="Quiet town square", target="Tracy City", likes=12)
        self.create_published_history(self.waterfalls, likes=200, days_ago=2)
        self.create_published_history(self.waterfalls, likes=20, days_ago=7, target="Greeter Falls")

        plan = build_smart_plan(self.workspace, count=3)

        strongest_item = next(item for item in plan["items"] if item["submission"].id == strongest.id)
        waterfall_insight = next(item for item in plan["accounts"] if item["account"].id == self.waterfalls.id)
        self.assertEqual(strongest_item["engagement_rank"], 1)
        self.assertEqual(strongest_item["account"], self.waterfalls)
        self.assertIn("fits TN Waterfalls", strongest_item["reason"])
        self.assertEqual(waterfall_insight["recent_count"], 2)
        self.assertGreater(strongest_item["scheduled_at"], timezone.now())

        response = self.client.get(
            reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Highest engagement first")
        self.assertContains(response, "Nothing changes until you confirm")
        self.assertNotContains(response, "IntersectionObserver")

    def test_one_tap_commit_creates_scheduled_posts_with_rights_credit_and_provenance(self):
        first = self.create_submission(
            title="Fall Creek Falls",
            target="Fall Creek Falls",
            likes=900,
            comments=40,
            body="Fall Creek Falls drops more than 250 feet.",
            handle="robinphoto",
        )
        second = self.create_submission(
            title="Greeter Falls",
            target="Greeter Falls",
            likes=500,
            body="A spring afternoon at Greeter Falls.",
            handle="trailfriend",
        )
        url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})
        preview = self.client.get(f"{url}?count=3")
        token = preview.context["plan_token"]

        committed = self.client.post(url, {"action": "commit", "plan_token": token})

        self.assertRedirects(
            committed,
            reverse("calendar:calendar", kwargs={"workspace_id": self.workspace.id}),
            fetch_redirect_response=False,
        )
        platform_posts = PlatformPost.objects.filter(
            post__performance_profile__source_submission_id__in=[first.id, second.id]
        ).select_related("post")
        self.assertEqual(platform_posts.count(), 2)
        self.assertTrue(all(item.status == PlatformPost.Status.SCHEDULED for item in platform_posts))
        self.assertTrue(all(item.scheduled_at > timezone.now() for item in platform_posts))
        first.refresh_from_db()
        first_post = ContentPerformanceProfile.objects.get(source_submission=first).post
        self.assertIn("@robinphoto", first_post.caption)
        self.assertEqual(first.metadata["smart_plan"]["mode"], "scheduled")
        self.assertTrue(first.metadata["studio_post_ids"])
        self.assertEqual(AuditEvent.objects.filter(action="ugc.smart_plan_scheduled").count(), 2)

        duplicate = self.client.post(url, {"action": "commit", "plan_token": token})
        self.assertEqual(duplicate.status_code, 302)
        self.assertEqual(ContentPerformanceProfile.objects.filter(source_submission=first).count(), 1)

    def test_plan_excludes_drafted_mismatched_and_account_blocked_content(self):
        ready = self.create_submission(title="Ready Greeter", target="Greeter Falls", likes=50)
        self.create_submission(
            title="Already drafted",
            target="Machine Falls",
            likes=5000,
            metadata_extra={"studio_post_ids": ["existing-post"]},
        )
        self.create_submission(
            title="Wrong target",
            target="Foster Falls",
            likes=4000,
            body="We loved Machine Falls in Tennessee.",
        )
        restricted = self.create_submission(
            title="Account restricted",
            target="Burgess Falls",
            likes=3000,
            allowed_account_ids=[str(self.general.id)],
        )

        waterfall_only = build_smart_plan(self.workspace, count=3, account_ids=[str(self.waterfalls.id)])

        planned_ids = {item["submission"].id for item in waterfall_only["items"]}
        self.assertEqual(planned_ids, {ready.id})
        self.assertNotIn(restricted.id, planned_ids)

    def test_required_approval_creates_timed_drafts_instead_of_bypassing_guard(self):
        submission = self.create_submission(title="Approval safe", target="Rock Island", likes=100)
        self.workspace.approval_workflow_mode = "required_internal"
        self.workspace.save(update_fields=["approval_workflow_mode", "updated_at"])
        url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})
        preview = self.client.get(url)
        self.assertFalse(preview.context["plan"]["direct_schedule"])
        self.assertContains(preview, "requires approval")

        response = self.client.post(
            url,
            {"action": "commit", "plan_token": preview.context["plan_token"]},
        )

        self.assertEqual(response.status_code, 302)
        profile = ContentPerformanceProfile.objects.get(source_submission=submission)
        variant = profile.post.platform_posts.get()
        self.assertEqual(variant.status, PlatformPost.Status.DRAFT)
        self.assertIsNone(variant.scheduled_at)
        self.assertIsNotNone(profile.post.proposed_publish_at)
        submission.refresh_from_db()
        self.assertEqual(submission.metadata["smart_plan"]["mode"], "approval_draft")

    def test_mobile_approved_queue_promotes_smart_plan(self):
        self.create_submission(title="Queue candidate", target="Cummins Falls", likes=100)
        response = self.client.get(
            reverse("ugc:moderation_queue", kwargs={"workspace_id": self.workspace.id})
            + "?tab=approved&draft_state=ready",
            HTTP_USER_AGENT="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Mobile",
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Smart plan approved posts")
        self.assertContains(response, reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id}))

    def test_posts_and_days_are_independent_and_fourteen_updates_the_plan(self):
        for index in range(16):
            self.create_submission(
                title=f"Plan option {index}",
                target=f"Tennessee stop {index}",
                likes=1000 - index,
            )
        url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})

        response = self.client.get(
            url,
            {
                "count": "14",
                "days": "30",
                "account_filter": "1",
                "accounts": [str(self.waterfalls.id), str(self.general.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_count"], 14)
        self.assertEqual(response.context["selected_days"], 30)
        self.assertEqual(response.context["plan"]["requested_count"], 14)
        self.assertEqual(response.context["plan"]["requested_days"], 30)
        self.assertContains(response, "14 posts")
        self.assertContains(response, "planned over 30 days")
        self.assertContains(response, 'data-plan-name="count" data-plan-value="14"')
        self.assertContains(response, 'nonce="')
        self.assertNotContains(response, "onclick=")
        self.assertTrue(
            all(item["scheduled_at"] < timezone.now() + timedelta(days=31) for item in response.context["plan"]["items"])
        )

    def test_edited_caption_and_hashtags_are_used_while_required_credit_is_preserved(self):
        submission = self.create_submission(
            title="Editable Burgess Falls",
            target="Burgess Falls",
            likes=1000,
            handle="waterfallcreator",
        )
        url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})
        preview = self.client.get(url, {"count": "1", "days": "30"})

        response = self.client.post(
            url,
            {
                "action": "commit",
                "plan_token": preview.context["plan_token"],
                f"caption_{submission.id}": "Would you hike here after a summer rain?\n\n#BurgessFalls #TennesseeWaterfalls",
            },
        )

        self.assertEqual(response.status_code, 302)
        caption = ContentPerformanceProfile.objects.get(source_submission=submission).post.caption
        self.assertIn("Would you hike here", caption)
        self.assertIn("#BurgessFalls", caption)
        self.assertIn("@waterfallcreator", caption)
        self.assertIn("📍 Burgess Falls", caption)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_CAPTION_MODEL="test-model")
    @patch("apps.common.ugc_smart_captions.httpx.post")
    def test_smart_captions_use_structured_ai_output_and_stay_editable(self, mocked_post):
        submission = self.create_submission(
            title="AI Foster Falls",
            target="Foster Falls",
            likes=5000,
            handle="smartcreator",
        )
        mocked_post.return_value = httpx.Response(
            200,
            request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
            json={
                "output_text": (
                    '{"captions":[{"id":"'
                    + str(submission.id)
                    + '","caption":"Save this Tennessee waterfall for your next free Saturday. '
                    'Would you take the overlook or the trail first?","hashtags":["#FosterFalls",'
                    '"#TennesseeWaterfalls","#ExploreTennessee"]}]}'
                )
            },
        )
        url = reverse("ugc:approved_smart_plan", kwargs={"workspace_id": self.workspace.id})

        response = self.client.get(
            url,
            {
                "count": "1",
                "days": "14",
                "smart_captions": "1",
                "account_filter": "1",
                "accounts": str(self.waterfalls.id),
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["caption_status"]["used_ai"])
        self.assertContains(response, "Save this Tennessee waterfall")
        self.assertContains(response, "#FosterFalls")
        self.assertContains(response, f'name="caption_{submission.id}"')
        request_json = mocked_post.call_args.kwargs["json"]
        self.assertEqual(request_json["model"], settings.OPENAI_CAPTION_MODEL)
        self.assertEqual(request_json["text"]["format"]["type"], "json_schema")
