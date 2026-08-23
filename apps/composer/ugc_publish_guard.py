"""Final rights and creator-credit checks for Studio post variants.

This module resolves the existing UGC submission and Rights Passport attached
to a Studio Post. It deliberately creates no parallel rights state: every
result is computed from the canonical submission, passport, account variant,
and effective caption at the moment it is checked.
"""

from __future__ import annotations

import re
import unicodedata

from django.core.exceptions import ObjectDoesNotExist

from apps.common.models import UGCSubmission
from apps.common.ugc_creator_services import rights_can_use

_UNSET = object()


def _legacy_ugc_map(workspace, post_ids):
    """Map older UGC drafts that predate ContentPerformanceProfile provenance."""
    wanted = {str(value) for value in post_ids}
    result = {}
    submissions = UGCSubmission.objects.for_workspace(workspace.id).select_related("rights_passport")
    for submission in submissions.iterator(chunk_size=250):
        for post_id in (submission.metadata or {}).get("studio_post_ids") or []:
            key = str(post_id)
            if key in wanted:
                result[key] = submission
    return result


def submission_for_post(post, legacy_map=None):
    try:
        profile = post.performance_profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if profile is not None and profile.source_submission_id:
        return profile.source_submission
    return (legacy_map or {}).get(str(post.id))


def find_submission_for_post(workspace, post):
    """Resolve current and legacy UGC provenance for one Studio Post."""
    if not getattr(post, "id", None):
        return None
    submission = submission_for_post(post)
    if submission is not None:
        return submission
    return submission_for_post(post, _legacy_ugc_map(workspace, [post.id]))


def account_rights(submission, account):
    """Return whether one canonical UGC asset may be used on one account."""
    if submission is None:
        return True, ""
    if submission.status != UGCSubmission.Status.APPROVED:
        return False, "Community content is no longer approved."
    if not submission.consent_confirmed:
        return False, "Contributor consent is required."
    allowed, error = rights_can_use(submission, "organic_social")
    if not allowed:
        return False, error
    passport = submission.rights_passport
    allowed_ids = {str(value) for value in (passport.allowed_account_ids or [])}
    if allowed_ids and str(account.id) not in allowed_ids:
        return False, "The Rights Passport does not allow this social account."
    return True, ""


def _normalized_credit(value):
    value = unicodedata.normalize("NFKC", str(value or "")).replace("\u200b", "")
    return re.sub(r"\s+", " ", value).strip().casefold()


def caption_has_required_credit(caption, credit_text):
    """Match the creator's saved credit text case-insensitively and intact."""
    required = _normalized_credit(credit_text)
    return bool(required and required in _normalized_credit(caption))


def _credit_blocker(submission, account, caption, *, platform_post=None):
    passport = submission.rights_passport
    if not passport.credit_required:
        return None
    credit_text = str(passport.credit_text or "").strip()
    base = {
        "account": account,
        "platform_post": platform_post,
        "platform_post_id": str(getattr(platform_post, "id", "") or ""),
        "social_account_id": str(account.id),
    }
    if not credit_text:
        return {
            **base,
            "code": "credit_not_recorded",
            "message": "The Rights Passport requires creator credit, but no exact credit text is recorded.",
        }
    if not caption_has_required_credit(caption, credit_text):
        return {
            **base,
            "code": "credit_missing",
            "message": f'Required creator credit “{credit_text}” is missing from this account caption.',
        }
    return None


def variant_preflight(submission, account, caption, *, platform_post=None):
    """Return blockers for one account's current effective UGC variant."""
    if submission is None:
        return []
    allowed, error = account_rights(submission, account)
    if not allowed:
        return [
            {
                "code": "rights_blocked",
                "message": error,
                "account": account,
                "platform_post": platform_post,
                "platform_post_id": str(getattr(platform_post, "id", "") or ""),
                "social_account_id": str(account.id),
            }
        ]
    blocker = _credit_blocker(submission, account, caption, platform_post=platform_post)
    return [blocker] if blocker else []


def post_publish_preflight(workspace, post, platform_posts=None, *, submission_override=_UNSET):
    """Check exact stored variants against the current Rights Passport."""
    submission = (
        find_submission_for_post(workspace, post)
        if submission_override is _UNSET
        else submission_override
    )
    variants = list(platform_posts) if platform_posts is not None else list(
        post.platform_posts.select_related("social_account")
    )
    blockers = []
    if submission is not None:
        for variant in variants:
            blockers.extend(
                variant_preflight(
                    submission,
                    variant.social_account,
                    variant.effective_caption or "",
                    platform_post=variant,
                )
            )
    passport = submission.rights_passport if submission is not None else None
    return {
        "is_ugc": submission is not None,
        "submission": submission,
        "passport": passport,
        "credit_required": bool(passport and passport.credit_required),
        "required_credit": str(passport.credit_text or "").strip() if passport else "",
        "blockers": blockers,
        "is_safe": not blockers,
    }


def payload_publish_preflight(workspace, post, account_captions):
    """Check unsaved composer captions before a schedule/queue mutation."""
    submission = find_submission_for_post(workspace, post)
    blockers = []
    if submission is not None:
        for account, caption in account_captions:
            blockers.extend(variant_preflight(submission, account, caption or ""))
    return {
        "is_ugc": submission is not None,
        "submission": submission,
        "blockers": blockers,
        "is_safe": not blockers,
    }
