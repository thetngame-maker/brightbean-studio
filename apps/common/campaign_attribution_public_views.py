"""Public redirect and TN Game conversion endpoints for attribution links."""

import hashlib
import json
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseGone, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .campaign_attribution import (
    is_bot_user_agent,
    record_attribution_click,
    record_registration,
    tracked_destination_url,
    verify_conversion_secret,
)
from .models import CampaignAttributionConversion, CampaignAttributionLink

CLICK_RATE_LIMIT = 300
CLICK_RATE_WINDOW = 60
CONVERSION_RATE_LIMIT = 120
CONVERSION_RATE_WINDOW = 60 * 60
MAX_CONVERSION_BODY_BYTES = 16 * 1024


def _client_ip(request):
    trusted = set(getattr(settings, "BB_TRUSTED_PROXIES", ()) or ())
    remote = request.META.get("REMOTE_ADDR") or "unknown"
    if trusted and remote in trusted:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR") or ""
        for hop in (value.strip() for value in forwarded.split(",") if value.strip()):
            if hop not in trusted:
                return hop
    return remote


def _rate_limited(prefix, value, *, limit, window):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:32]
    key = f"{prefix}:{digest}"
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, window)
        count = 1
    return count > limit


def _available_link(code):
    link = get_object_or_404(CampaignAttributionLink.objects.select_related("workspace"), code=code)
    if not link.is_active or link.archived_at or (link.expires_at and link.expires_at <= timezone.now()):
        return link, False
    return link, True


@require_GET
def attribution_redirect(request, code):
    link, available = _available_link(code)
    if not available:
        return HttpResponseGone("This campaign link is no longer active.")
    user_agent = request.META.get("HTTP_USER_AGENT") or ""
    rate_key = f"{link.code}|{_client_ip(request)}"
    if not is_bot_user_agent(user_agent) and not _rate_limited(
        "attribution-click",
        rate_key,
        limit=CLICK_RATE_LIMIT,
        window=CLICK_RATE_WINDOW,
    ):
        record_attribution_click(
            link,
            client_ip=_client_ip(request),
            user_agent=user_agent,
        )
    response = HttpResponseRedirect(tracked_destination_url(link))
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _conversion_error(message, status):
    response = JsonResponse({"ok": False, "error": message}, status=status)
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_POST
def attribution_conversion(request, code):
    link, available = _available_link(code)
    if not available:
        return _conversion_error("link_inactive", 410)
    if len(request.body) > MAX_CONVERSION_BODY_BYTES:
        return _conversion_error("body_too_large", 413)
    rate_key = f"{link.code}|{_client_ip(request)}"
    if _rate_limited(
        "attribution-conversion",
        rate_key,
        limit=CONVERSION_RATE_LIMIT,
        window=CONVERSION_RATE_WINDOW,
    ):
        return _conversion_error("rate_limited", 429)
    secret = request.headers.get("X-TN-Attribution-Key") or ""
    if not verify_conversion_secret(link, secret):
        return _conversion_error("invalid_key", 401)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        return _conversion_error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _conversion_error("invalid_json", 400)
    event_id = str(payload.get("event_id") or "").strip()
    if not event_id or len(event_id) > 500:
        return _conversion_error("event_id_required", 400)
    occurred_at = parse_datetime(str(payload.get("occurred_at") or "")) or timezone.now()
    if timezone.is_naive(occurred_at):
        occurred_at = timezone.make_aware(occurred_at, timezone.get_current_timezone())
    now = timezone.now()
    if occurred_at > now + timedelta(minutes=5) or occurred_at < now - timedelta(days=1095):
        return _conversion_error("occurred_at_out_of_range", 400)
    conversion, created = record_registration(
        link,
        external_id=event_id,
        occurred_at=occurred_at,
        source=CampaignAttributionConversion.Source.WEBHOOK,
        quantity=1,
        metadata=payload.get("metadata"),
    )
    response = JsonResponse(
        {"ok": True, "created": created, "conversion_id": str(conversion.id)},
        status=201 if created else 200,
    )
    response["Cache-Control"] = "no-store"
    return response
