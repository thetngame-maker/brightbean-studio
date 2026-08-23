from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.analytics.models import PostInsightsSnapshot
from apps.composer.models import PlatformPost, Post
from apps.social_accounts.models import SocialAccount

from ..models import AuditEvent, ContentPerformanceProfile, UGCRightsPassport, UGCSubmission
from ..ugc_performance_learning import build_performance_learning


class UGCPerformanceLearningTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email="performance-learning@example.com",
            password="testpass123",
            tos_accepted_at=timezone.now(),
        )
        self.workspace = self.user.workspace_memberships.select_related("workspace").get().workspace
        self.workspace.discovery_searches = [
            {
                "id": "learning-target",
                "name": "Foster Falls",
                "target_type": "top_sight",
                "target_id": "foster-falls",
                "target_label": "Foster Falls",
            }
        ]
        self.workspace.save(update_fields=["discovery_searches", "updated_at"])
        self.account = SocialAccount.objects.create(
            workspace=self.workspace,
            platform="instagram_login",
            account_platform_id="tn-game-instagram",
            account_name="TN Game Instagram",
            account_handle="thetngame",
            oauth_access_token="token",
            connection_status=SocialAccount.ConnectionStatus.CONNECTED,
        )
        self.client.force_login(self.user)

    def create_published_post(self, *, hook, likes, index):
        post = Post.objects.create(
            workspace=self.workspace,
            author=self.user,
            title=f"Learning post {index}",
            caption=f"Foster Falls learning example {index}",
        )
        platform_post = PlatformPost.objects.create(
            post=post,
            social_account=self.account,
            status=PlatformPost.Status.PUBLISHED,
            platform_post_id=f"instagram-{index}",
            published_at=timezone.now() - timedelta(days=index),
        )
        ContentPerformanceProfile.objects.create(
            workspace=self.workspace,
            post=post,
            source_type=ContentPerformanceProfile.SourceType.BRANDED,
            opening_hook=hook,
            caption_style=ContentPerformanceProfile.CaptionStyle.GUIDE,
            season=ContentPerformanceProfile.Season.SPRING,
            subject=ContentPerformanceProfile.Subject.WATERFALL,
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            created_by=self.user,
            updated_by=self.user,
        )
        today = timezone.localdate()
        PostInsightsSnapshot.objects.create(
            platform_post=platform_post,
            metric_key="reach",
            date=today,
            value=100,
        )
        PostInsightsSnapshot.objects.create(
            platform_post=platform_post,
            metric_key="likes",
            date=today,
            value=likes,
        )
        return platform_post

    def test_learning_loop_turns_repeat_account_evidence_into_a_lesson(self):
        person_one = self.create_published_post(
            hook=ContentPerformanceProfile.OpeningHook.PERSON_ON_CAMERA,
            likes=20,
            index=1,
        )
        self.create_published_post(
            hook=ContentPerformanceProfile.OpeningHook.PERSON_ON_CAMERA,
            likes=20,
            index=2,
        )
        self.create_published_post(
            hook=ContentPerformanceProfile.OpeningHook.SCENIC_REVEAL,
            likes=5,
            index=3,
        )
        self.create_published_post(
            hook=ContentPerformanceProfile.OpeningHook.SCENIC_REVEAL,
            likes=5,
            index=4,
        )

        learning = build_performance_learning(self.workspace, days=90)
        person_row = next(row for row in learning["rows"] if row["platform_post"].id == person_one.id)
        self.assertEqual(learning["counts"]["with_analytics"], 4)
        self.assertEqual(person_row["relative_index"], 160)
        self.assertTrue(any("person on camera" in lesson["title"] for lesson in learning["lessons"]))
        self.assertTrue(any("TN Game Instagram" in lesson["title"] for lesson in learning["lessons"]))

        response = self.client.get(reverse("ugc:performance_learning", kwargs={"workspace_id": self.workspace.id}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Performance Learning")
        self.assertContains(response, "60% above this account’s baseline")
        self.assertEqual(len(response.context["learning_rows"]), 4)

    def test_profile_update_reuses_target_catalog_and_is_audited(self):
        platform_post = self.create_published_post(
            hook=ContentPerformanceProfile.OpeningHook.SCENIC_REVEAL,
            likes=10,
            index=1,
        )
        update_url = reverse(
            "ugc:update_performance_profile",
            kwargs={"workspace_id": self.workspace.id, "platform_post_id": platform_post.id},
        )
        return_to = reverse("ugc:performance_learning", kwargs={"workspace_id": self.workspace.id})
        response = self.client.post(
            update_url,
            {
                "source_type": "ugc",
                "opening_hook": "action_first",
                "caption_style": "story",
                "season": "summer",
                "subject": "trail",
                "target_key": "top_sight::foster-falls",
                "notes": "People enter the frame immediately.",
                "return_to": return_to,
            },
        )

        profile = platform_post.post.performance_profile
        profile.refresh_from_db()
        self.assertRedirects(response, return_to, fetch_redirect_response=False)
        self.assertEqual(profile.source_type, ContentPerformanceProfile.SourceType.UGC)
        self.assertEqual(profile.opening_hook, ContentPerformanceProfile.OpeningHook.ACTION_FIRST)
        self.assertEqual(profile.target_label, "Foster Falls")
        self.assertTrue(
            AuditEvent.objects.filter(action="ugc.performance_profile_updated", target_id=str(profile.id)).exists()
        )

    def test_ugc_draft_records_learning_provenance_automatically(self):
        submission = UGCSubmission.objects.create(
            workspace=self.workspace,
            kind=UGCSubmission.Kind.COMMUNITY_POST,
            status=UGCSubmission.Status.APPROVED,
            source=UGCSubmission.Source.IMPORT,
            contributor_handle="waterfall_creator",
            target_type="top_sight",
            target_id="foster-falls",
            target_label="Foster Falls",
            title="Fresh Foster Falls",
            body="Current conditions at Foster Falls in Tennessee.",
            consent_confirmed=True,
            consent_at=timezone.now(),
            metadata={
                "provenance": {"platform": "instagram", "discovery_source": "saved_search"},
                "permission": {"status": "granted"},
            },
        )
        passport = submission.rights_passport
        passport.status = UGCRightsPassport.Status.GRANTED
        passport.allow_organic_social = True
        passport.save(update_fields=["status", "allow_organic_social", "updated_at"])
        draft_url = reverse(
            "ugc:use_in_post",
            kwargs={"workspace_id": self.workspace.id, "submission_id": submission.id},
        )

        response = self.client.post(draft_url)

        profile = ContentPerformanceProfile.objects.get(source_submission=submission)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(profile.source_type, ContentPerformanceProfile.SourceType.UGC)
        self.assertEqual(profile.creator_id, submission.creator_id)
        self.assertEqual(profile.target_id, "foster-falls")
