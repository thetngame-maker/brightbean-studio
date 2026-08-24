"""Editable engagement-focused caption drafts for Approved Smart Plan."""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from django.conf import settings

from .ugc_smart_planning import _caption_for_account

logger = logging.getLogger(__name__)


AI_ERRORS = (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError)


def _hashtag(value):
    clean = re.sub(r"[^A-Za-z0-9]", "", str(value or "").lstrip("#"))
    return f"#{clean}" if clean else ""


def _fallback_hashtags(workspace, item):
    submission = item["submission"]
    account = item["account"]
    raw = [
        *(workspace.default_hashtags or []),
        submission.target_label,
        "Tennessee",
        "TennesseeWaterfalls" if "fall" in str(submission.target_label or "").casefold() else "ExploreTennessee",
        "TennesseeTravel",
        "VisitTennessee",
        account.account_handle,
    ]
    tags = []
    for value in raw:
        tag = _hashtag(value)
        if tag and tag.casefold() not in {existing.casefold() for existing in tags}:
            tags.append(tag)
    return tags[:8]


def _fallback_lead(item):
    submission = item["submission"]
    target = submission.target_label or submission.title or "this Tennessee stop"
    source = (submission.body or submission.title or "").strip().rstrip()
    hook = f"Would you add {target} to your Tennessee list?"
    question = "What would you want to know before visiting?"
    return "\n\n".join(part for part in (hook, source, question) if part)


def _compose(workspace, item, lead, hashtags):
    tags = []
    for value in hashtags or []:
        tag = _hashtag(value)
        if tag and tag.casefold() not in {existing.casefold() for existing in tags}:
            tags.append(tag)
    if not tags:
        tags = _fallback_hashtags(workspace, item)
    body = "\n\n".join(part for part in (str(lead or "").strip(), " ".join(tags[:10])) if part)
    return _caption_for_account(item["submission"], item["account"], lead_override=body)


def _response_text(payload):
    if payload.get("output_text"):
        return payload["output_text"]
    for output in payload.get("output") or []:
        for content in output.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return ""


def _excerpt(value, *, limit):
    clean = re.sub(r"\s+", " ", str(value or "")).strip()
    return clean if len(clean) <= limit else f"{clean[: limit - 1].rstrip()}…"


def _chunks(values, size):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def _ai_batch(workspace, items, *, api_key):
    inputs = []
    for item in items:
        submission = item["submission"]
        inputs.append(
            {
                "id": str(submission.id),
                "account": item["account"].display_label,
                "platform": item["account"].platform,
                "title": _excerpt(submission.title, limit=180),
                "location_or_subject": _excerpt(submission.target_label, limit=180),
                "source_caption": _excerpt(submission.body, limit=2400),
                "creator": submission.contributor_handle or submission.contributor_name,
                "source_engagement": item["engagement_label"],
                "recent_high-performing_captions": [
                    _excerpt(example, limit=700) for example in (item.get("winner_examples") or [])[:3]
                ],
            }
        )
    schema = {
        "type": "object",
        "properties": {
            "captions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "caption": {"type": "string"},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["id", "caption", "hashtags"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["captions"],
        "additionalProperties": False,
    }
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": getattr(settings, "OPENAI_CAPTION_MODEL", "gpt-5-mini"),
            "instructions": (
                "Write editable social captions designed to earn genuine engagement for a Tennessee travel "
                "brand. Use only facts in the supplied content. Start with a specific, natural hook; keep the "
                "voice warm and local; include one easy question or save/share prompt when appropriate. Learn "
                "tone patterns from the recent winners without copying them. Return 5-10 specific, relevant "
                "hashtags, avoiding spammy or unrelated tags. Do not add creator credit or location lines; the "
                "application appends those safely."
            ),
            "input": json.dumps({"posts": inputs}, ensure_ascii=False),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "smart_caption_batch",
                    "strict": True,
                    "schema": schema,
                }
            },
        },
        timeout=max(10, int(getattr(settings, "OPENAI_CAPTION_TIMEOUT", 60))),
    )
    response.raise_for_status()
    parsed = json.loads(_response_text(response.json()))
    return {str(row["id"]): row for row in parsed.get("captions") or []}


def _ai_drafts(workspace, items):
    api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
    if not api_key:
        return None, len(items)
    batch_size = max(1, min(4, int(getattr(settings, "OPENAI_CAPTION_BATCH_SIZE", 2))))
    batches = list(_chunks(items, batch_size))
    worker_count = max(1, min(len(batches), 8, int(getattr(settings, "OPENAI_CAPTION_MAX_WORKERS", 4))))
    generated = {}
    failed_items = 0
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="smart-caption") as executor:
        futures = {executor.submit(_ai_batch, workspace, batch, api_key=api_key): batch for batch in batches}
        for future in as_completed(futures):
            batch = futures[future]
            try:
                generated.update(future.result())
            except AI_ERRORS:
                failed_items += len(batch)
                logger.exception("Smart Plan AI caption batch failed", extra={"batch_size": len(batch)})
    return generated, failed_items


def build_caption_drafts(workspace, items, *, use_ai=False):
    """Attach safe, editable caption drafts and report how they were made."""
    generated = None
    if use_ai:
        try:
            generated, _ = _ai_drafts(workspace, items)
        except AI_ERRORS:
            logger.exception("Smart Plan AI caption generation failed")
    for item in items:
        submission_id = str(item["submission"].id)
        row = (generated or {}).get(submission_id) or {}
        item["caption_is_ai"] = bool(row)
        if use_ai:
            lead = row.get("caption") or _fallback_lead(item)
            hashtags = row.get("hashtags") or _fallback_hashtags(workspace, item)
            item["caption_draft"] = _compose(workspace, item, lead, hashtags)
        else:
            item["caption_draft"] = _caption_for_account(item["submission"], item["account"])
    if not use_ai:
        return {
            "requested": False,
            "used_ai": False,
            "generated_count": 0,
            "tone": "idle",
            "message": "Turn on Smart captions to draft hooks and hashtags.",
        }
    generated_count = len(generated or {})
    if items and generated_count == len(items):
        return {
            "requested": True,
            "used_ai": True,
            "generated_count": generated_count,
            "tone": "success",
            "message": "AI captions and hashtags are ready to edit.",
        }
    if generated_count:
        return {
            "requested": True,
            "used_ai": True,
            "generated_count": generated_count,
            "tone": "partial",
            "message": (
                f"AI drafted {generated_count} of {len(items)} captions. "
                "Fallback drafts filled the rest and everything is editable."
            ),
        }
    return {
        "requested": True,
        "used_ai": False,
        "generated_count": 0,
        "tone": "fallback",
        "message": "Smart fallback drafts are ready to edit; AI generation is temporarily unavailable.",
    }
