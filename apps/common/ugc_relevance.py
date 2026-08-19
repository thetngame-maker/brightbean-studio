"""Conservative relevance scoring for externally discovered UGC."""

from __future__ import annotations

import re
from typing import Any

_COMMON_TOKENS = {
    "the", "a", "an", "and", "or", "of", "at", "in", "on", "to",
    "fall", "falls", "waterfall", "waterfalls", "trail", "trails", "park",
    "state", "tn", "tennessee",
}

# Explicit geographic signals that are useful for catching obvious keyword-search
# false positives. We only use these when there is no Tennessee/target signal.
_NON_TN_MARKERS = {
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
    "connecticut", "delaware", "florida", "georgia", "hawaii", "idaho",
    "illinois", "indiana", "iowa", "kansas", "kentucky", "louisiana",
    "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada",
    "new hampshire", "new jersey", "new mexico", "new york",
    "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota",
    "texas", "utah", "vermont", "virginia", "washington", "west virginia",
    "wisconsin", "wyoming", "canada", "british columbia", "alberta",
    "ontario", "quebec", "revelstoke", "banff", "vancouver",
}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", value) if token not in _COMMON_TOKENS and len(token) > 2]


def score_relevance(raw: dict[str, Any], *, query: str = "", target_label: str = "") -> dict[str, Any]:
    """Return strong/possible/low relevance without pretending to be semantic AI.

    Low is intentionally reserved for explicit geographic contradictions. Neutral
    rows are kept as possible so short captions do not cause false rejections.
    """
    query_text = _clean(query)
    target_text = _clean(target_label)
    caption = _clean(raw.get("caption") or raw.get("body"))
    location = _clean(raw.get("location_name"))
    source_title = _clean(raw.get("source_title") or raw.get("raw_title"))
    haystack = " ".join(part for part in (caption, location, source_title) if part)

    phrases = []
    for phrase in (query_text, target_text):
        if phrase and phrase not in phrases:
            phrases.append(phrase)

    distinctive = []
    for phrase in phrases:
        distinctive.extend(_tokens(phrase))
    distinctive = list(dict.fromkeys(distinctive))

    exact_match = any(phrase and phrase in haystack for phrase in phrases)
    token_hits = [token for token in distinctive if re.search(rf"\b{re.escape(token)}\b", haystack)]
    tennessee_signal = bool(re.search(r"\btennessee\b|\btn\b", haystack))

    contradiction = ""
    if not exact_match and not token_hits and not tennessee_signal:
        for marker in sorted(_NON_TN_MARKERS, key=len, reverse=True):
            if re.search(rf"\b{re.escape(marker)}\b", haystack):
                contradiction = marker
                break
        if not contradiction and re.search(r"(?:^|[\s,])bc(?:[\s,.]|$)", haystack):
            contradiction = "bc"

    if exact_match or (tennessee_signal and token_hits):
        status = "strong"
        score = 100
        reason = "Target place is explicitly referenced."
    elif contradiction:
        status = "low"
        score = 5
        reason = f"Explicit geographic mismatch: {contradiction}."
    elif token_hits or tennessee_signal:
        status = "possible"
        score = 65
        reason = "Some target or Tennessee context is present."
    else:
        status = "possible"
        score = 45
        reason = "No explicit mismatch; needs human review."

    return {
        "relevance_status": status,
        "relevance_score": score,
        "relevance_reason": reason,
    }


def tag_relevance(rows, *, query: str = "", target_label: str = ""):
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row.update(score_relevance(row, query=query, target_label=target_label))
    return rows
