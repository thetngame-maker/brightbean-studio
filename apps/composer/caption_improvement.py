"""AI-assisted caption rewriting for the post composer."""

from __future__ import annotations

import json
import logging
import uuid

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from apps.common.audit import record_audit_event
from apps.members.decorators import require_permission
from apps.social_accounts.models import SocialAccount

from .models import Post
from .ugc_publish_guard import caption_has_required_credit, post_publish_preflight

logger = logging.getLogger(__name__)

AI_ERRORS = (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError)
MAX_SOURCE_CAPTION_LENGTH = 10_000
DEFAULT_TARGET_LENGTH = 1_800


def _workspace(request, workspace_id):
    # Import lazily to avoid coupling this small endpoint to composer startup.
    from . import views

    return views._get_workspace(request, workspace_id)


def _response_text(payload):
    if payload.get("output_text"):
        return payload["output_text"]
    for output in payload.get("output") or []:
        for content in output.get("content") or []:
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return ""


def _account_ids(raw):
    values = []
    for value in (raw or "").split(",")[:50]:
        try:
            values.append(uuid.UUID(value.strip()))
        except (AttributeError, TypeError, ValueError):
            continue
    return values


def _append_required_credit(caption, required_credit):
    caption = str(caption or "").strip()
    required_credit = str(required_credit or "").strip()
    if required_credit and not caption_has_required_credit(caption, required_credit):
        caption = f"{caption}\n\n{required_credit}" if caption else required_credit
    return caption


def generate_improved_caption(
    *,
    source_caption,
    title="",
    account_labels=None,
    target_length=DEFAULT_TARGET_LENGTH,
    required_credit="",
    api_key,
):
    """Return a stronger factual caption while preserving required credit."""
    schema = {
        "type": "object",
        "properties": {"caption": {"type": "string"}},
        "required": ["caption"],
        "additionalProperties": False,
    }
    response = httpx.post(
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": getattr(settings, "OPENAI_CAPTION_MODEL", "gpt-5-mini"),
            "instructions": (
                "Rewrite the supplied social caption for a Tennessee travel and community brand. Preserve every "
                "place name, creator attribution, and concrete fact, and never invent facts that are not supplied. "
                "Make the opening specific and natural, use short mobile-friendly paragraphs, and add one useful "
                "question or save/share prompt when appropriate. Keep only relevant hashtags and avoid generic "
                "filler, hype, clickbait, and references to AI. Return only the improved caption in the schema. "
                "Stay within the supplied maximum character count."
            ),
            "input": json.dumps(
                {
                    "title": str(title or "").strip()[:255],
                    "previous_caption": str(source_caption or "").strip(),
                    "destinations": list(account_labels or []),
                    "maximum_characters": int(target_length),
                    "protected_creator_credit": str(required_credit or "").strip(),
                },
                ensure_ascii=False,
            ),
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "improved_composer_caption",
                    "strict": True,
                    "schema": schema,
                }
            },
        },
        timeout=max(10, int(getattr(settings, "OPENAI_CAPTION_TIMEOUT", 60))),
    )
    response.raise_for_status()
    payload = json.loads(_response_text(response.json()))
    improved = _append_required_credit(payload.get("caption"), required_credit)
    if not improved:
        raise ValueError("AI returned an empty caption")
    return improved


@login_required
@require_permission("create_posts")
@require_POST
def improve_caption(request, workspace_id):
    """Generate a proposal without changing the saved Post until the user applies it."""
    workspace = _workspace(request, workspace_id)
    previous_caption = request.POST.get("caption", "").strip()
    title = request.POST.get("title", "").strip()
    if not previous_caption:
        return JsonResponse({"error": "Add a caption before asking AI to improve it."}, status=400)
    if len(previous_caption) > MAX_SOURCE_CAPTION_LENGTH:
        return JsonResponse({"error": "This caption is too long to improve in one request."}, status=400)

    post = None
    post_id = request.POST.get("post_id", "").strip()
    if post_id:
        post = get_object_or_404(Post, id=post_id, workspace=workspace)

    accounts = list(
        SocialAccount.objects.filter(
            workspace=workspace,
            id__in=_account_ids(request.POST.get("selected_accounts", "")),
        )
    )
    account_labels = [f"{account.get_platform_display()}: {account.display_label}" for account in accounts]
    target_length = min([account.char_limit for account in accounts] or [DEFAULT_TARGET_LENGTH])
    target_length = min(target_length, DEFAULT_TARGET_LENGTH)

    required_credit = ""
    if post is not None:
        required_credit = post_publish_preflight(workspace, post)["required_credit"]

    api_key = getattr(settings, "OPENAI_API_KEY", "").strip()
    if not api_key:
        return JsonResponse(
            {"error": "AI caption improvement is not configured for this workspace yet."},
            status=503,
        )

    try:
        improved = generate_improved_caption(
            source_caption=previous_caption,
            title=title,
            account_labels=account_labels,
            target_length=target_length,
            required_credit=required_credit,
            api_key=api_key,
        )
    except AI_ERRORS:
        logger.exception(
            "Composer AI caption improvement failed",
            extra={"workspace_id": str(workspace.id), "post_id": str(post_id or "")},
        )
        return JsonResponse(
            {"error": "AI could not improve this caption right now. Your previous caption is unchanged."},
            status=502,
        )

    record_audit_event(
        workspace=workspace,
        action="composer.caption_ai_generated",
        actor=request.user,
        target=post,
        target_type="composer.post" if post is not None else "composer.draft",
        target_id=str(post.id) if post is not None else "new",
        target_label=post.title if post is not None else title,
        metadata={
            "model": getattr(settings, "OPENAI_CAPTION_MODEL", "gpt-5-mini"),
            "previous_length": len(previous_caption),
            "suggested_length": len(improved),
            "target_length": target_length,
            "required_credit_preserved": bool(required_credit),
        },
        request=request,
    )
    return JsonResponse(
        {
            "previous_caption": previous_caption,
            "suggested_caption": improved,
            "target_length": target_length,
            "over_limit": len(improved) > target_length,
        }
    )
