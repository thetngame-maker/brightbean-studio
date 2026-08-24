"""Keyword-backed, review-first suggestions for Community bulk moderation."""

import re

DEFAULT_GRANT_KEYWORDS = (
    "Tennessee",
    "TN",
    "Nashville",
    "Memphis",
    "Knoxville",
    "Chattanooga",
    "Gatlinburg",
    "Pigeon Forge",
    "Great Smoky Mountains",
    "Smoky Mountains",
    "South Cumberland",
    "Fall Creek Falls",
    "Foster Falls",
    "Greeter Falls",
    "Burgess Falls",
    "Cummins Falls",
    "Rock Island State Park",
    "Big South Fork",
)

DEFAULT_REMOVE_KEYWORDS = (
    "NY",
    "NYC",
    "New York",
    "PNW",
    "Pacific Northwest",
    "California",
    "Colorado",
    "Oregon",
    "Washington State",
    "Washington coast",
    "Florida",
    "Texas",
    "Arizona",
    "Utah",
    "North Carolina",
    "South Carolina",
    "Georgia",
    "Alabama",
    "Kentucky",
    "Virginia",
    "West Virginia",
    "Ohio",
    "Michigan",
    "Maine",
    "Vermont",
    "New Hampshire",
    "Massachusetts",
)


def _clean_keywords(values, defaults):
    if not isinstance(values, (list, tuple)):
        values = defaults
    cleaned = []
    seen = set()
    for value in values:
        keyword = str(value or "").strip()[:64]
        normalized = keyword.casefold()
        if not keyword or normalized in seen:
            continue
        seen.add(normalized)
        cleaned.append(keyword)
    return cleaned


def workspace_smart_rules(workspace):
    stored = workspace.community_smart_rules if isinstance(workspace.community_smart_rules, dict) else {}
    return {
        "grant": _clean_keywords(stored.get("grant"), DEFAULT_GRANT_KEYWORDS),
        "remove": _clean_keywords(stored.get("remove"), DEFAULT_REMOVE_KEYWORDS),
    }


def normalize_smart_rules(grant_keywords, remove_keywords):
    return {
        "grant": _clean_keywords(grant_keywords, ()),
        "remove": _clean_keywords(remove_keywords, ()),
    }


def _matches_keyword(text, keyword):
    pattern = rf"(?<!\w){re.escape(keyword.casefold())}(?!\w)"
    return bool(re.search(pattern, text))


def smart_selection_for(submission, rules):
    """Return a suggestion without changing permission or moderation state."""
    metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
    discovery = metadata.get("discovery_import") if isinstance(metadata.get("discovery_import"), dict) else {}
    provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
    searchable = " \n ".join(
        str(value or "")
        for value in (
            submission.title,
            submission.body,
            submission.target_label,
            discovery.get("caption"),
            discovery.get("location_name"),
            discovery.get("location"),
            discovery.get("hashtags"),
            provenance.get("discovery_query"),
        )
    ).casefold()

    grant_matches = [keyword for keyword in rules["grant"] if _matches_keyword(searchable, keyword)]
    remove_matches = [keyword for keyword in rules["remove"] if _matches_keyword(searchable, keyword)]
    if grant_matches and not remove_matches:
        decision = "grant"
        reason = "Tennessee match: " + ", ".join(grant_matches[:3])
    elif remove_matches and not grant_matches:
        decision = "remove"
        reason = "Outside-Tennessee match: " + ", ".join(remove_matches[:3])
    elif grant_matches and remove_matches:
        decision = "review"
        reason = "Conflicting location matches — review manually"
    else:
        decision = "review"
        reason = "No location keyword match"
    return {
        "decision": decision,
        "reason": reason,
        "grant_matches": grant_matches,
        "remove_matches": remove_matches,
    }
