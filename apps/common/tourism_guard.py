"""Explainable tourism safety and accuracy preflight checks.

The guard intentionally uses stored Studio text, target metadata, and
source-backed workspace rules. It makes no provider, weather, trail, or social
API calls while rendering or publishing.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from django.core.exceptions import ObjectDoesNotExist

from apps.composer.models import PlatformPost, Post

from .models import TourismGuardReview, TourismGuardRule

GUARDED_STATUSES = {
    PlatformPost.Status.DRAFT,
    PlatformPost.Status.PENDING_REVIEW,
    PlatformPost.Status.APPROVED,
    PlatformPost.Status.CHANGES_REQUESTED,
    PlatformPost.Status.REJECTED,
    PlatformPost.Status.SCHEDULED,
    PlatformPost.Status.FAILED,
    PlatformPost.Status.ON_HOLD,
}
DEFAULT_SAFE_CONTEXT = ("not allowed", "prohibited", "do not", "don't", "never attempt", "avoid")
SEVERITY_RANK = {"blocker": 0, "warning": 1, "reminder": 2}


@dataclass(frozen=True)
class BuiltinRule:
    key: str
    kind: str
    severity: str
    title: str
    guidance: str
    trigger_phrases: tuple[str, ...]
    safe_context_phrases: tuple[str, ...] = DEFAULT_SAFE_CONTEXT
    source_label: str = "TN Social Studio safety standard"
    source_url: str = ""
    target_type: str = ""
    target_id: str = ""
    target_label: str = "All Tennessee content"
    is_builtin: bool = True


BUILTIN_RULES = (
    BuiltinRule(
        key="builtin:dangerous-jumps",
        kind=TourismGuardRule.Kind.SAFETY,
        severity=TourismGuardRule.Severity.BLOCKER,
        title="Dangerous jumping claim",
        guidance=(
            "Do not encourage jumping from waterfalls, cliffs, overlooks, bridges, or other elevated natural features. "
            "Revise the caption or verify that it clearly communicates the prohibition."
        ),
        trigger_phrases=(
            "cliff jump",
            "cliff jumping",
            "waterfall jump",
            "waterfall jumping",
            "jump off the falls",
            "jump from the waterfall",
            "jumping from waterfalls",
        ),
    ),
    BuiltinRule(
        key="builtin:guaranteed-safety",
        kind=TourismGuardRule.Kind.ACCURACY,
        severity=TourismGuardRule.Severity.WARNING,
        title="Absolute safety claim",
        guidance=(
            "Conditions change. Replace absolute safety language with specific, current guidance and encourage visitors "
            "to check official alerts and use judgment."
        ),
        trigger_phrases=("completely safe", "always safe", "no danger", "risk-free", "safe in all conditions"),
    ),
    BuiltinRule(
        key="builtin:guaranteed-access",
        kind=TourismGuardRule.Kind.ACCESS,
        severity=TourismGuardRule.Severity.WARNING,
        title="Unverified access or closure claim",
        guidance=(
            "Hours, roads, trails, permits, and closures can change. Verify the claim against an official source and "
            "avoid presenting access as guaranteed."
        ),
        trigger_phrases=("always open", "never closes", "open year-round", "no permit needed", "no reservation needed"),
    ),
    BuiltinRule(
        key="builtin:guaranteed-swimming",
        kind=TourismGuardRule.Kind.SAFETY,
        severity=TourismGuardRule.Severity.WARNING,
        title="Swimming claim needs conditions context",
        guidance=(
            "Water depth, flow, weather, bacteria, closures, and site rules change. Verify swimming or wading language "
            "and avoid guaranteeing that the water is safe."
        ),
        trigger_phrases=("safe to swim", "perfectly safe for swimming", "swimming is always allowed"),
    ),
    BuiltinRule(
        key="builtin:accessibility-absolute",
        kind=TourismGuardRule.Kind.ACCESSIBILITY,
        severity=TourismGuardRule.Severity.WARNING,
        title="Accessibility claim needs verification",
        guidance=(
            "Accessibility varies by route, surface, grade, facility, and current conditions. Verify specific features "
            "instead of using an absolute accessibility claim."
        ),
        trigger_phrases=("fully accessible", "accessible to everyone", "wheelchair accessible everywhere"),
    ),
)


def _custom_rule_dict(rule):
    return {
        "key": f"rule:{rule.id}",
        "kind": rule.kind,
        "severity": rule.severity,
        "title": rule.title,
        "guidance": rule.guidance,
        "trigger_phrases": tuple(str(value).strip().lower() for value in rule.trigger_phrases if str(value).strip()),
        "safe_context_phrases": tuple(
            str(value).strip().lower()
            for value in (rule.safe_context_phrases or DEFAULT_SAFE_CONTEXT)
            if str(value).strip()
        ),
        "source_label": rule.source_label or "Official source",
        "source_url": rule.source_url,
        "target_type": rule.target_type,
        "target_id": rule.target_id,
        "target_label": rule.target_label,
        "is_builtin": False,
        "model": rule,
        "updated_at": rule.updated_at.isoformat(),
    }


def guard_rules(workspace, *, active_only=True):
    custom = TourismGuardRule.objects.for_workspace(workspace.id)
    if active_only:
        custom = custom.filter(is_active=True)
    custom_rules = [_custom_rule_dict(rule) for rule in custom]
    builtins = [rule.__dict__ for rule in BUILTIN_RULES]
    return [*builtins, *custom_rules], custom_rules


def _post_target(post):
    try:
        profile = post.performance_profile
    except (AttributeError, ObjectDoesNotExist):
        profile = None
    if profile is None:
        return {"target_type": "", "target_id": "", "target_label": ""}
    return {
        "target_type": profile.target_type,
        "target_id": profile.target_id,
        "target_label": profile.target_label,
    }


def _variant_texts(post):
    variants = list(post.platform_posts.all())
    if not variants:
        return [(None, "\n".join((post.title or "", post.caption or "")))]
    return [
        (variant, "\n".join((variant.effective_title or "", variant.effective_caption or ""))) for variant in variants
    ]


def _target_matches(rule, target, combined_text):
    if rule.get("is_builtin"):
        return True
    if (
        target["target_type"]
        and target["target_id"]
        and target["target_type"] == rule["target_type"]
        and target["target_id"] == rule["target_id"]
    ):
        return True
    label = str(rule.get("target_label") or "").strip().casefold()
    return bool(label and label in combined_text.casefold())


def _sentence_matches(text, phrases, safe_context):
    sentences = [part.strip().casefold() for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", text) if part.strip()]
    matches = []
    for phrase in phrases:
        normalized = str(phrase or "").strip().casefold()
        if not normalized:
            continue
        for sentence in sentences:
            if normalized not in sentence:
                continue
            if any(context in sentence for context in safe_context):
                continue
            matches.append(normalized)
            break
    return sorted(set(matches))


def _rule_signature(rule):
    values = {
        key: rule.get(key)
        for key in (
            "key",
            "kind",
            "severity",
            "title",
            "guidance",
            "trigger_phrases",
            "safe_context_phrases",
            "source_url",
            "target_type",
            "target_id",
            "updated_at",
        )
    }
    return json.dumps(values, sort_keys=True, default=str)


def _finding_fingerprint(post, rule, variant_texts):
    content = {
        "post_id": str(post.id),
        "title": post.title,
        "variants": [{"id": str(variant.id) if variant else "base", "text": text} for variant, text in variant_texts],
        "rule": _rule_signature(rule),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def _scan_post(post, rules, reviews=None):
    reviews = reviews or {}
    target = _post_target(post)
    variant_texts = _variant_texts(post)
    combined_text = "\n".join(text for _variant, text in variant_texts)
    findings = []
    verified = []
    for rule in rules:
        if not _target_matches(rule, target, combined_text):
            continue
        triggers = tuple(rule.get("trigger_phrases") or ())
        safe_context = tuple(rule.get("safe_context_phrases") or DEFAULT_SAFE_CONTEXT)
        matched = []
        accounts = []
        if triggers:
            for variant, text in variant_texts:
                variant_matches = _sentence_matches(text, triggers, safe_context)
                if not variant_matches:
                    continue
                matched.extend(variant_matches)
                if variant is not None:
                    accounts.append(variant.social_account.display_label)
            if not matched:
                continue
        fingerprint = _finding_fingerprint(post, rule, variant_texts)
        finding = {
            "rule": rule,
            "rule_key": rule["key"],
            "severity": rule["severity"],
            "matched_phrases": sorted(set(matched)),
            "affected_accounts": sorted(set(accounts)),
            "fingerprint": fingerprint,
            "target": target if target["target_label"] else {"target_label": rule.get("target_label") or ""},
        }
        review = reviews.get((post.id, rule["key"]))
        if review is not None and review.finding_fingerprint == fingerprint:
            finding["review"] = review
            verified.append(finding)
        else:
            findings.append(finding)
    findings.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 9), item["rule"]["title"]))
    verified.sort(key=lambda item: (SEVERITY_RANK.get(item["severity"], 9), item["rule"]["title"]))
    return findings, verified


def build_tourism_guard(workspace, *, limit=300):
    rules, custom_rules = guard_rules(workspace)
    posts = list(
        Post.objects.for_workspace(workspace.id)
        .filter(platform_posts__status__in=GUARDED_STATUSES)
        .select_related("performance_profile")
        .prefetch_related("platform_posts__social_account")
        .distinct()
        .order_by("-updated_at")[:limit]
    )
    reviews = {
        (review.post_id, review.rule_key): review
        for review in TourismGuardReview.objects.for_workspace(workspace.id)
        .filter(post_id__in=[post.id for post in posts])
        .select_related("reviewed_by")
    }
    rows = []
    verified_rows = []
    for post in posts:
        findings, verified = _scan_post(post, rules, reviews)
        if findings:
            rows.append(
                {"post": post, "findings": findings, "blocker_count": sum(f["severity"] == "blocker" for f in findings)}
            )
        if verified:
            verified_rows.append({"post": post, "findings": verified})
    counts = {
        "posts": len(rows),
        "blockers": sum(finding["severity"] == "blocker" for row in rows for finding in row["findings"]),
        "warnings": sum(finding["severity"] == "warning" for row in rows for finding in row["findings"]),
        "reminders": sum(finding["severity"] == "reminder" for row in rows for finding in row["findings"]),
        "verified": sum(len(row["findings"]) for row in verified_rows),
        "custom_rules": len(custom_rules),
    }
    return {
        "rows": rows,
        "verified_rows": verified_rows,
        "counts": counts,
        "custom_rules": custom_rules,
        "builtins": [rule.__dict__ for rule in BUILTIN_RULES],
        "limit_reached": len(posts) == limit,
    }


def findings_for_post(workspace, post_id):
    rules, _custom = guard_rules(workspace)
    post = (
        Post.objects.for_workspace(workspace.id)
        .select_related("performance_profile")
        .prefetch_related("platform_posts__social_account")
        .get(id=post_id)
    )
    reviews = {
        (review.post_id, review.rule_key): review
        for review in TourismGuardReview.objects.for_workspace(workspace.id).filter(post=post)
    }
    findings, verified = _scan_post(post, rules, reviews)
    return post, findings, verified


def blocking_findings_for_post(workspace, post_id):
    _post, findings, _verified = findings_for_post(workspace, post_id)
    return [finding for finding in findings if finding["severity"] == TourismGuardRule.Severity.BLOCKER]
