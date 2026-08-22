"""Small, deterministic quality checks for the lightweight mobile UGC workflow."""

import re


_NAMED_FALL_RE = re.compile(r"\b([A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,2}\s+Falls?)\b")


def _normalise(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).split())


def approved_quality(submission):
    """Return a conservative pre-draft quality warning for an approved item.

    We only flag an obvious named-waterfall mismatch when the attached target is
    itself a Falls target, the caption does not mention that target, and the
    caption explicitly names a different Falls location. Low relevance is also
    surfaced, but we deliberately avoid guessing from images or generic text.
    """
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
            }

    if getattr(submission, "mobile_relevance_status", "") == "low":
        return {
            "needs_check": True,
            "reason": "This approved item has a low relevance score. Double-check it before creating a draft.",
            "kind": "low_relevance",
            "suggested_target_label": "",
        }

    return {"needs_check": False, "reason": "", "kind": "", "suggested_target_label": ""}


def decorate_approved_quality(submission):
    quality = approved_quality(submission)
    submission.mobile_needs_quality_check = quality["needs_check"]
    submission.mobile_quality_reason = quality["reason"]
    submission.mobile_quality_kind = quality["kind"]
    submission.mobile_suggested_target_label = quality.get("suggested_target_label", "")
    return submission
