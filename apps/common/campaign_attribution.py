"""First-party campaign attribution without storing raw visitor identifiers."""

from __future__ import annotations

import ipaddress
import secrets
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F
from django.utils import timezone
from django.utils.crypto import constant_time_compare, salted_hmac

from .models import (
    CampaignAttributionClick,
    CampaignAttributionConversion,
    CampaignAttributionLink,
)

BOT_MARKERS = (
    "bot",
    "crawler",
    "spider",
    "facebookexternalhit",
    "linkedinbot",
    "pinterestbot",
    "slackbot",
    "discordbot",
    "telegrambot",
    "whatsapp",
)


def is_public_https_destination(value: str) -> bool:
    """Syntactically allow public HTTPS destinations without a server-side fetch."""
    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            return False
        try:
            ip = ipaddress.ip_address(hostname)
        except ValueError:
            return "." in hostname
        return not (
            ip.is_private
            or ip.is_reserved
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
        )
    except (TypeError, ValueError):
        return False


def generate_conversion_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_conversion_secret(secret: str) -> str:
    return salted_hmac("campaign-attribution-secret", str(secret or ""), secret=settings.SECRET_KEY).hexdigest()


def verify_conversion_secret(link, secret: str) -> bool:
    return bool(secret) and constant_time_compare(link.conversion_secret_hash, hash_conversion_secret(secret))


def create_attribution_link(*, conversion_secret: str, **fields):
    """Create a link with collision-safe public code generation."""
    fields["conversion_secret_hash"] = hash_conversion_secret(conversion_secret)
    fields["conversion_secret_hint"] = conversion_secret[-6:]
    for _attempt in range(5):
        try:
            return CampaignAttributionLink.objects.create(**fields)
        except IntegrityError:
            fields.pop("code", None)
    raise RuntimeError("Could not allocate a unique attribution link code.")


def rotate_conversion_secret(link, secret: str, *, actor=None):
    link.conversion_secret_hash = hash_conversion_secret(secret)
    link.conversion_secret_hint = secret[-6:]
    link.updated_by = actor if getattr(actor, "is_authenticated", False) else None
    link.save(update_fields=["conversion_secret_hash", "conversion_secret_hint", "updated_by", "updated_at"])


def tracked_destination_url(link) -> str:
    """Attach transparent UTM values and the TN Game attribution reference."""
    parsed = urlsplit(link.destination_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    for key, value in (
        ("utm_source", link.utm_source),
        ("utm_medium", link.utm_medium),
        ("utm_campaign", link.utm_campaign),
        ("tng_ref", link.code),
    ):
        if value:
            query[key] = value
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment))


def is_bot_user_agent(user_agent: str) -> bool:
    lowered = str(user_agent or "").lower()
    return any(marker in lowered for marker in BOT_MARKERS)


def _workspace_zone(workspace):
    try:
        return ZoneInfo(workspace.effective_timezone or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def _visitor_hash(link, *, day, client_ip: str, user_agent: str) -> str:
    # The date intentionally rotates the pseudonym every local day. Studio can
    # report unique daily visitors without building a cross-day visitor profile.
    value = f"{link.id}|{day.isoformat()}|{client_ip or 'unknown'}|{str(user_agent or '')[:300]}"
    return salted_hmac("campaign-attribution-visitor", value, secret=settings.SECRET_KEY).hexdigest()


def record_attribution_click(link, *, client_ip: str, user_agent: str, occurred_at=None):
    """Record one click and one privacy-preserving unique daily visitor."""
    occurred_at = occurred_at or timezone.now()
    day = timezone.localdate(occurred_at, _workspace_zone(link.workspace))
    visitor_hash = _visitor_hash(
        link,
        day=day,
        client_ip=client_ip,
        user_agent=user_agent,
    )
    with transaction.atomic():
        aggregate, created = CampaignAttributionClick.objects.get_or_create(
            link=link,
            day=day,
            visitor_hash=visitor_hash,
            defaults={"clicks": 1, "first_clicked_at": occurred_at, "last_clicked_at": occurred_at},
        )
        if not created:
            CampaignAttributionClick.objects.filter(id=aggregate.id).update(
                clicks=F("clicks") + 1,
                last_clicked_at=occurred_at,
            )
        updates = {"click_count": F("click_count") + 1, "last_clicked_at": occurred_at}
        if created:
            updates["unique_visitor_count"] = F("unique_visitor_count") + 1
        CampaignAttributionLink.objects.filter(id=link.id).update(**updates)
    return aggregate, created


def _external_id_hash(link, external_id: str) -> str:
    return salted_hmac(
        "campaign-attribution-conversion",
        f"{link.id}|{external_id}",
        secret=settings.SECRET_KEY,
    ).hexdigest()


def _safe_metadata(metadata) -> dict:
    if not isinstance(metadata, dict):
        return {}
    result = {}
    for key in ("registration_type", "source", "campaign"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            result[key] = value.strip()[:100]
    return result


def record_registration(
    link,
    *,
    external_id: str,
    occurred_at: datetime,
    source: str,
    quantity=1,
    note="",
    metadata=None,
    actor=None,
):
    """Idempotently add a registration without retaining its raw external ID."""
    external_id = str(external_id or "").strip()
    if not external_id:
        raise ValueError("external_id is required")
    quantity = int(quantity)
    if quantity < 1 or quantity > 1_000_000:
        raise ValueError("quantity must be between 1 and 1,000,000")
    external_hash = _external_id_hash(link, external_id[:500])
    with transaction.atomic():
        conversion, created = CampaignAttributionConversion.objects.get_or_create(
            link=link,
            external_id_hash=external_hash,
            defaults={
                # The display reference comes from the one-way hash, not from
                # any portion of the source system's raw event identifier.
                "external_id_hint": external_hash[:8],
                "source": source,
                "quantity": quantity,
                "occurred_at": occurred_at,
                "note": str(note or "").strip()[:500],
                "metadata": _safe_metadata(metadata),
                "recorded_by": actor if getattr(actor, "is_authenticated", False) else None,
            },
        )
        if created:
            CampaignAttributionLink.objects.filter(id=link.id).update(
                registration_count=F("registration_count") + quantity,
                last_converted_at=occurred_at,
            )
    return conversion, created


def link_conversion_rate(link):
    if not link.unique_visitor_count:
        return None
    return round((link.registration_count / link.unique_visitor_count) * 100, 1)
