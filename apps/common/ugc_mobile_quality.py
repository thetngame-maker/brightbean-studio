"""Small, deterministic quality checks for the lightweight mobile UGC workflow."""

import hashlib
import re

from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone

_NAMED_FALL_RE = re.compile(r"\b([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}\s+Falls?)\b")


def _normalise(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())


def _quality_fingerprint(submission):
    """Fingerprint the fields that can change the current quality decision."""
    relevance = getattr(submission, "mobile_relevance_status", "") or ""
    rights = ""
    try:
        passport = submission.rights_passport
    except (AttributeError, ObjectDoesNotExist):
        passport = None
    if passport is not None:
        rights = "|".join(
            [
                str(passport.status),
                str(bool(passport.allow_organic_social)),
                passport.expires_at.isoformat() if passport.expires_at else "",
            ]
        )
    payload = "|".join(
        [
            _normalise(submission.target_label or submission.title or ""),
            _normalise(submission.body or ""),
            _normalise(relevance),
            rights,
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:20]


def approved_quality(submission):
    """Return a conservative pre-draft quality warning for an approved item.

    We only flag an obvious named-waterfall mismatch when the attached target is
    itself a Falls target, the caption does not mention that target, and the
    caption explicitly names a different Falls location. Low relevance is also
    surfaced, but we deliberately avoid guessing from images or generic text.

    A moderator can explicitly mark the *current* warning as reviewed. The
    override is fingerprinted, so changing the caption, target, or relevance
    automatically invalidates it and allows the warning to return.
    """
    fingerprint = _quality_fingerprint(submission)
    try:
        passport = submission.rights_passport
    except (AttributeError, ObjectDoesNotExist):
        passport = None
    if passport is not None:
        rights_expired = bool(passport.expires_at and passport.expires_at <= timezone.now())
        if rights_expired:
            return {
                "needs_check": True,
                "reason": "Creator permission has expired. Update the rights passport before creating a draft.",
                "kind": "rights",
                "suggested_target_label": "",
                "reviewed_override": False,
                "fingerprint": fingerprint,
            }
        if passport.status != "granted" or not passport.allow_organic_social:
            detail = (
                f"Rights are {passport.get_status_display().lower()}."
                if passport.status != "granted"
                else "Organic social use is not included in the granted rights."
            )
            return {
                "needs_check": True,
                "reason": f"{detail} Update the rights passport before creating a draft.",
                "kind": "rights",
                "suggested_target_label": "",
                "reviewed_override": False,
                "fingerprint": fingerprint,
            }
    override = (submission.metadata or {}).get("approved_quality_override") or {}
    if override.get("fingerprint") == fingerprint:
        return {
            "needs_check": False,
            "reason": "",
            "kind": "",
            "suggested_target_label": "",
            "reviewed_override": True,
            "fingerprint": fingerprint,
        }

    target = (submission.target_label or submission.title or "").strip()
    body = (submission.body or "").strip()
    target_norm = _normalise(target)
    body_norm = _normalise(body)

    if target and body and "fall" in target_norm and target_norm not in body_norm:
        mentions = []
        for match in _NAMED_FALL_RE.findall(body):
            mention = match.strip()
            mention_norm = _normalise(mention)
            if mention_norm and mention_norm != target_norm and mention not in mentions:
                mentions.append(mention)
        if mentions:
            named = mentions[0]
            return {
                "needs_check": True,
                "reason": f"Caption mentions {named}, but this item is attached to {target}.",
                "kind": "target_mismatch",
                "suggested_target_label": named,
                "reviewed_override": False,
                "fingerprint": fingerprint,
            }

    if getattr(submission, "mobile_relevance_status", "") == "low":
        return {
            "needs_check": True,
            "reason": "This approved item has a low relevance score. Double-check it before creating a draft.",
            "kind": "low_relevance",
            "suggested_target_label": "",
            "reviewed_override": False,
            "fingerprint": fingerprint,
        }

    return {
        "needs_check": False,
        "reason": "",
        "kind": "",
        "suggested_target_label": "",
        "reviewed_override": False,
        "fingerprint": fingerprint,
    }


def decorate_approved_quality(submission):
    quality = approved_quality(submission)
    submission.mobile_needs_quality_check = quality["needs_check"]
    submission.mobile_quality_reason = quality["reason"]
    submission.mobile_quality_kind = quality["kind"]
    submission.mobile_suggested_target_label = quality.get("suggested_target_label", "")
    submission.mobile_quality_reviewed_override = quality.get("reviewed_override", False)
    submission.mobile_quality_fingerprint = quality.get("fingerprint", "")
    return submission
