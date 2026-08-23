"""Staff creation and public response views for collaboration invitations."""

from __future__ import annotations

import hashlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCCreatorCollaboration
from .ugc_creator_collaboration_invites import (
    ACCEPTED,
    CollaborationInviteError,
    create_collaboration_invite,
    expire_collaboration_invite,
    find_collaboration_invite,
    respond_to_collaboration_invite,
)
from .ugc_creator_views import _get_workspace, _safe_local_path

PUBLIC_RESPONSE_LIMIT = 30
PUBLIC_RESPONSE_WINDOW = 60 * 60
RIGHTS_LABELS = {
    "organic_social": "Organic social",
    "website": "TN Game website",
    "email": "Email/newsletters",
    "paid_ads": "Paid advertising",
    "print": "Print materials",
}


def creator_collaboration_public_url(request, invite):
    return request.build_absolute_uri(
        reverse("creator_collaboration_public:respond", kwargs={"token": invite.request_token})
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_collaboration_invite_view(request, workspace_id, collaboration_id):
    workspace = _get_workspace(request, workspace_id)
    collaboration = get_object_or_404(
        UGCCreatorCollaboration.objects.for_workspace(workspace.id).select_related("creator"),
        id=collaboration_id,
    )
    fallback = reverse(
        "ugc:creator_collaboration_detail",
        kwargs={"workspace_id": workspace.id, "collaboration_id": collaboration.id},
    )
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    try:
        expires_in_days = int(request.POST.get("expires_in_days") or 14)
    except (TypeError, ValueError):
        expires_in_days = 14
    try:
        invite, superseded_count = create_collaboration_invite(
            collaboration,
            actor=request.user,
            expires_in_days=expires_in_days,
        )
    except CollaborationInviteError as exc:
        messages.error(request, str(exc))
        return redirect(return_to)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_collaboration_invite_created",
        target=collaboration,
        metadata={
            "collaboration_id": str(collaboration.id),
            "invite_id": str(invite.id),
            "terms_digest": invite.terms_digest,
            "expires_at": invite.expires_at.isoformat(),
            "superseded_count": superseded_count,
        },
        request=request,
    )
    messages.success(request, "Secure creator collaboration link ready. Copy the invitation and send it.")
    return redirect(return_to)


def _client_key(request, invite):
    remote = request.META.get("REMOTE_ADDR") or "unknown"
    digest = hashlib.sha256(f"{invite.id}|{remote}".encode()).hexdigest()[:32]
    return f"creator-collaboration-response:{digest}"


def _rate_limited(request, invite):
    key = _client_key(request, invite)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, PUBLIC_RESPONSE_WINDOW)
        count = 1
    return count > PUBLIC_RESPONSE_LIMIT


def _public_response(request, invite, *, error="", status=200):
    snapshot = invite.terms_snapshot or {}
    identities = list(invite.collaboration.creator.identities.all())
    primary = next((identity for identity in identities if identity.is_primary), identities[0] if identities else None)
    due_at = parse_datetime(str(snapshot.get("content_due_at") or ""))
    response = render(
        request,
        "ugc/creator_collaboration_public.html",
        {
            "invite": invite,
            "collaboration": invite.collaboration,
            "workspace": invite.workspace,
            "terms": snapshot,
            "content_due_at": due_at,
            "requested_rights_labels": [
                RIGHTS_LABELS[value]
                for value in snapshot.get("requested_rights", [])
                if value in RIGHTS_LABELS
            ],
            "creator_label": invite.collaboration.creator.display_name
            or (f"@{primary.handle}" if primary and primary.handle else "Creator"),
            "collaboration_error": error,
        },
        status=status,
    )
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@require_http_methods(["GET", "POST"])
def creator_collaboration_public_view(request, token):
    invite = find_collaboration_invite(token)
    if invite is None:
        raise Http404("Collaboration invitation not found.")
    expire_collaboration_invite(invite)
    if request.method == "POST" and invite.status == invite.Status.PENDING:
        if _rate_limited(request, invite):
            return _public_response(request, invite, error="Too many attempts. Please try again later.", status=429)
        action = str(request.POST.get("action") or "").strip().lower()
        if action == ACCEPTED and request.POST.get("agreement_confirmed") != "1":
            return _public_response(
                request,
                invite,
                error="Confirm that you understand this collaboration brief before accepting.",
                status=400,
            )
        try:
            invite, _changed = respond_to_collaboration_invite(
                invite,
                action=action,
                response_note=request.POST.get("response_note"),
            )
        except CollaborationInviteError as exc:
            return _public_response(request, invite, error=str(exc), status=400)
    return _public_response(request, invite)
