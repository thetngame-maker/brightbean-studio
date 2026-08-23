"""Stored-data progress metrics for community content missions."""

from django.core.exceptions import ObjectDoesNotExist

from .models import UGCRightsPassport, UGCSubmission
from .ugc_mobile_quality import approved_quality


def decorate_content_mission(mission):
    """Attach rights-safe mission progress without provider or social API calls."""
    submissions = (
        UGCSubmission.objects.for_workspace(mission.workspace_id)
        .filter(
            target_type=mission.target_type,
            target_id=mission.target_id,
            submitted_at__gte=mission.starts_at,
        )
        .exclude(status__in=[UGCSubmission.Status.REJECTED, UGCSubmission.Status.REMOVED])
        .select_related("rights_passport")
        .only(
            "id",
            "status",
            "creator_id",
            "metadata",
            "title",
            "body",
            "target_label",
            "submitted_at",
            "rights_passport__status",
            "rights_passport__allow_organic_social",
            "rights_passport__expires_at",
        )
        .order_by("-submitted_at")[:500]
    )
    capture_count = 0
    rights_count = 0
    ready_count = 0
    drafted_count = 0
    creators = set()
    latest_content_at = None
    for submission in submissions:
        capture_count += 1
        latest_content_at = latest_content_at or submission.submitted_at
        if submission.creator_id:
            creators.add(submission.creator_id)
        try:
            passport = submission.rights_passport
        except (AttributeError, ObjectDoesNotExist, UGCRightsPassport.DoesNotExist):
            passport = None
        rights_active = bool(passport and passport.is_active and passport.allow_organic_social)
        if rights_active:
            rights_count += 1
        if (submission.metadata or {}).get("studio_post_ids"):
            drafted_count += 1
        if (
            submission.status == UGCSubmission.Status.APPROVED
            and rights_active
            and not approved_quality(submission)["needs_check"]
        ):
            ready_count += 1

    mission.capture_count = capture_count
    mission.rights_count = rights_count
    mission.ready_count = ready_count
    mission.drafted_count = drafted_count
    mission.creator_count = len(creators)
    mission.latest_content_at = latest_content_at
    mission.goal_remaining = max(0, mission.goal_count - ready_count)
    mission.goal_met = ready_count >= mission.goal_count
    mission.progress_percentage = min(100, round((ready_count / max(1, mission.goal_count)) * 100))
    return mission
