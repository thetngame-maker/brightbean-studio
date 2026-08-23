"""Reusable TN Game target catalog for UGC discovery and correction workflows."""

from collections import defaultdict

from django.db.models import Count

from .models import UGCSubmission
from .ugc_mobile_quality import _normalise


def _clean(value, limit=255):
    return str(value or "").strip()[:limit]


def build_target_catalog(workspace, *, limit=300):
    """Merge known targets from UGC, saved discovery searches, and learned aliases.

    This deliberately stays migration-free for the first catalog version. It gives
    every workflow one canonical in-memory target list while we prove the UX before
    introducing a dedicated synced TN Game entity table.
    """
    targets = {}

    def ensure(target_type, target_id, target_label="", target_url=""):
        target_type = _clean(target_type, 100)
        target_id = _clean(target_id, 255)
        if not target_type or not target_id:
            return None
        key = (target_type, target_id)
        item = targets.setdefault(
            key,
            {
                "target_type": target_type,
                "target_id": target_id,
                "target_label": "",
                "target_url": "",
                "ugc_count": 0,
                "discovery_count": 0,
                "sources": set(),
                "aliases": set(),
            },
        )
        label = _clean(target_label, 255)
        url = _clean(target_url, 2000)
        if label and (not item["target_label"] or len(label) > len(item["target_label"])):
            item["target_label"] = label
        if url and not item["target_url"]:
            item["target_url"] = url
        return item

    # Community content gives us real usage counts and the latest known labels/URLs.
    rows = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .exclude(target_type="")
        .exclude(target_id="")
        .values("target_type", "target_id", "target_label", "target_url")
        .annotate(use_count=Count("id"))
        .order_by("-use_count", "target_label")[:1000]
    )
    for row in rows:
        item = ensure(row["target_type"], row["target_id"], row["target_label"], row["target_url"])
        if not item:
            continue
        item["ugc_count"] += int(row.get("use_count") or 0)
        item["sources"].add("community")

    # Saved discovery searches often know a target before any UGC has been imported.
    for search in list(workspace.discovery_searches or [])[:200]:
        if not isinstance(search, dict):
            continue
        item = ensure(
            search.get("target_type"),
            search.get("target_id"),
            search.get("target_label"),
            search.get("target_url"),
        )
        if not item:
            continue
        item["discovery_count"] += 1
        item["sources"].add("discovery")

    # Corrections teach aliases without silently changing historical submissions.
    recent = (
        UGCSubmission.objects.for_workspace(workspace.id)
        .only("metadata")
        .order_by("-updated_at")[:500]
    )
    for submission in recent:
        correction = (submission.metadata or {}).get("target_correction") or {}
        target_type = _clean(correction.get("to_target_type"), 100)
        target_id = _clean(correction.get("to_target_id"), 255)
        alias = _clean(correction.get("alias"), 255)
        if not target_type or not target_id or not alias:
            continue
        item = ensure(
            target_type,
            target_id,
            correction.get("to_target_label"),
            correction.get("to_target_url"),
        )
        if item:
            item["aliases"].add(alias)
            item["sources"].add("learned")

    result = []
    for item in targets.values():
        if not item["target_label"]:
            item["target_label"] = item["target_id"]
        item["aliases"] = sorted(item["aliases"], key=str.lower)
        item["sources"] = sorted(item["sources"])
        item["usage_count"] = item["ugc_count"] + item["discovery_count"]
        item["picker_value"] = f'{item["target_type"]}::{item["target_id"]}'
        result.append(item)

    result.sort(key=lambda x: (-x["usage_count"], x["target_label"].lower(), x["target_type"]))
    return result[:limit]


def target_choices(workspace, *, suggested_label="", current_submission=None, limit=80):
    """Return picker-ready targets with caption/learned suggestions ranked first."""
    catalog = build_target_catalog(workspace, limit=max(limit * 4, 200))
    suggested_norm = _normalise(suggested_label)
    current_key = None
    if current_submission is not None:
        current_key = (current_submission.target_type, current_submission.target_id)

    for item in catalog:
        key = (item["target_type"], item["target_id"])
        alias_norms = {_normalise(alias) for alias in item.get("aliases", [])}
        exact_match = bool(suggested_norm and _normalise(item["target_label"]) == suggested_norm)
        alias_match = bool(suggested_norm and suggested_norm in alias_norms)
        item["is_current"] = key == current_key
        item["is_suggested"] = exact_match or alias_match
        item["suggestion_source"] = "caption" if exact_match else ("learned" if alias_match else "")

    catalog.sort(
        key=lambda item: (
            not item["is_suggested"],
            item["is_current"],
            -item["usage_count"],
            item["target_label"].lower(),
        )
    )
    return catalog[:limit]


def learned_target_for_text(workspace, text, *, current_label=""):
    """Resolve one explicit human-taught alias from text, or return None.

    This is intentionally conservative. It never guesses from canonical target
    names and it never chooses between competing learned aliases. If the text
    already names the current/default target, the saved discovery target wins.
    """
    text_norm = _normalise(text)
    current_norm = _normalise(current_label)
    if not text_norm or (current_norm and current_norm in text_norm):
        return None

    matches = {}
    for item in build_target_catalog(workspace, limit=500):
        key = (item["target_type"], item["target_id"])
        for alias in item.get("aliases", []):
            alias_norm = _normalise(alias)
            if not alias_norm or alias_norm not in text_norm:
                continue
            match = matches.setdefault(key, {"target": item, "aliases": []})
            match["aliases"].append(alias)

    # One unique target is required. Ambiguity is intentionally left for human review.
    if len(matches) != 1:
        return None
    match = next(iter(matches.values()))
    return {
        "target": match["target"],
        "alias": sorted(match["aliases"], key=lambda value: (-len(value), value.lower()))[0],
    }


def find_catalog_target(workspace, target_type, target_id):
    key = (_clean(target_type, 100), _clean(target_id, 255))
    for item in build_target_catalog(workspace, limit=500):
        if (item["target_type"], item["target_id"]) == key:
            return item
    return None
