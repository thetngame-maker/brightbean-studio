"""Staff creation and public creator response views for secure rights requests."""

from __future__ import annotations

import hashlib

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCCreator, UGCSubmission
from .ugc_creator_rights_requests import (
    SCOPE_FIELDS,
    RightsRequestError,
    create_creator_rights_request,
    expire_creator_rights_request,
    find_creator_rights_request,
    requested_scopes,
    respond_to_creator_rights_request,
)
from .ugc_creator_views import _safe_local_path
from .ugc_permissions import GRANTED, get_permission
from .ugc_views import _discovered_q, _get_workspace

PUBLIC_RESPONSE_LIMIT = 30
PUBLIC_RESPONSE_WINDOW = 60 * 60


def creator_rights_public_url(request, rights_request):
    return request.build_absolute_uri(
        reverse("creator_rights_public:respond", kwargs={"token": rights_request.request_token})
    )


def _checked(request, name, default=False):
    if name not in request.POST:
        return default
    return str(request.POST.get(name) or "").lower() in {"1", "true", "yes", "on"}


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_creator_rights_request_view(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id}) + "?tab=discovered"
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    submission = get_object_or_404(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(status=UGCSubmission.Status.PENDING)
        .filter(_discovered_q())
        .select_related("creator"),
        id=submission_id,
    )
    if get_permission(submission.metadata)["status"] == GRANTED or submission.consent_confirmed:
        messages.info(request, "This creator has already granted permission.")
        return redirect(return_to)
    if submission.creator_id and submission.creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT:
        messages.error(request, "This creator is marked Do not contact. Update the relationship before outreach.")
        return redirect(return_to)
    try:
        expires_in_days = int(request.POST.get("expires_in_days") or 14)
    except (TypeError, ValueError):
        expires_in_days = 14
    credit = (
        (submission.creator.preferred_credit if submission.creator_id else "")
        or (f"@{submission.contributor_handle.strip().lstrip('@')}" if submission.contributor_handle else "")
        or submission.contributor_name
    )
    scope_form = request.POST.get("scope_form") == "1"
    try:
        rights_request, superseded_count = create_creator_rights_request(
            submission,
            actor=request.user,
            expires_in_days=expires_in_days,
            allow_organic_social=_checked(request, "allow_organic_social", not scope_form),
            allow_website=_checked(request, "allow_website", not scope_form),
            allow_email=_checked(request, "allow_email"),
            allow_paid_ads=_checked(request, "allow_paid_ads"),
            allow_print=_checked(request, "allow_print"),
            credit_required=_checked(request, "credit_required", not scope_form),
            credit_text=str(request.POST.get("credit_text") or credit).strip()[:500],
        )
    except RightsRequestError as exc:
        messages.error(request, str(exc))
        return redirect(return_to)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_rights_request_created",
        target=rights_request,
        metadata={
            "submission_id": str(submission.id),
            "requested_scopes": requested_scopes(rights_request),
            "expires_at": rights_request.expires_at.isoformat(),
            "credit_required": rights_request.credit_required,
            "superseded_count": superseded_count,
        },
        request=request,
    )
    messages.success(request, "Secure creator permission link ready. Copy the request and send it on Instagram.")
    return redirect(return_to)


def _client_key(request, rights_request):
    remote = request.META.get("REMOTE_ADDR") or "unknown"
    digest = hashlib.sha256(f"{rights_request.id}|{remote}".encode()).hexdigest()[:32]
    return f"creator-rights-response:{digest}"


def _rate_limited(request, rights_request):
    key = _client_key(request, rights_request)
    try:
        count = cache.incr(key)
    except ValueError:
        cache.set(key, 1, PUBLIC_RESPONSE_WINDOW)
        count = 1
    return count > PUBLIC_RESPONSE_LIMIT


def _scope_options(rights_request):
    granted = set(rights_request.granted_scopes or [])
    return [
        {
            "key": key,
            "label": label,
            "requested": bool(getattr(rights_request, field)),
            "granted": key in granted,
        }
        for key, field, label in SCOPE_FIELDS
        if getattr(rights_request, field)
    ]


def _public_response(request, rights_request, *, error="", status=200):
    response = render(
        request,
        "ugc/creator_rights_public.html",
        {
            "rights_request": rights_request,
            "submission": rights_request.submission,
            "workspace": rights_request.workspace,
            "scope_options": _scope_options(rights_request),
            "creator_rights_error": error,
        },
        status=status,
    )
    response["Cache-Control"] = "no-store, private"
    response["Referrer-Policy"] = "no-referrer"
    response["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    return response


@require_http_methods(["GET", "POST"])
def creator_rights_public_view(request, token):
    rights_request = find_creator_rights_request(token)
    if rights_request is None:
        raise Http404("Rights request not found.")
    expire_creator_rights_request(rights_request)
    if rights_request.submission.status in {
        UGCSubmission.Status.REJECTED,
        UGCSubmission.Status.REMOVED,
    } and rights_request.status == rights_request.Status.PENDING:
        rights_request.status = rights_request.Status.CANCELLED
        rights_request.save(update_fields=["status", "updated_at"])
    if request.method == "POST" and rights_request.status == rights_request.Status.PENDING:
        if _rate_limited(request, rights_request):
            return _public_response(request, rights_request, error="Too many attempts. Please try again later.", status=429)
        action = str(request.POST.get("action") or "").strip().lower()
        if action == GRANTED and request.POST.get("consent_confirmed") != "1":
            return _public_response(
                request,
                rights_request,
                error="Confirm that you understand the selected usage before granting permission.",
                status=400,
            )
        try:
            rights_request, _created = respond_to_creator_rights_request(
                rights_request,
                action=action,
                selected_scopes=request.POST.getlist("scopes"),
                credit_text=request.POST.get("credit_text"),
            )
        except RightsRequestError as exc:
            return _public_response(request, rights_request, error=str(exc), status=400)
    return _public_response(request, rights_request)
