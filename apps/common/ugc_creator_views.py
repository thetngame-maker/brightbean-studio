"""Server-rendered creator relationship hub and per-asset rights passports."""

from __future__ import annotations

from datetime import datetime, time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import URLValidator
from django.db.models import Count, Max, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.social_accounts.models import SocialAccount

from .audit import record_audit_event
from .models import AuditEvent, UGCCreator, UGCCreatorIdentity, UGCRightsPassport, UGCSubmission
from .ugc_creator_opportunities import (
    OPPORTUNITY_LABELS,
    classify_creator_opportunity,
    creator_engagement_score,
)
from .ugc_creator_services import sync_rights_passport_from_submission
from .ugc_permissions import DECLINED, GRANTED, NOT_CONTACTED, REQUESTED, set_permission
from .ugc_views import _get_workspace

CREATOR_PAGE_SIZE = 12
CREATOR_RADAR_LIMIT = 500


def _safe_local_path(request, value, fallback):
    value = str(value or "").strip()
    if (
        value.startswith("/")
        and not value.startswith("//")
        and url_has_allowed_host_and_scheme(
            value,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        )
    ):
        return value
    return fallback


def _primary_identity(creator):
    identities = list(creator.identities.all())
    return next((item for item in identities if item.is_primary), identities[0] if identities else None)


def _creator_queryset(workspace):
    identities = UGCCreatorIdentity.objects.order_by("-is_primary", "platform", "normalized_handle")
    return (
        UGCCreator.objects.for_workspace(workspace.id)
        .prefetch_related(Prefetch("identities", queryset=identities))
        .annotate(
            content_count=Count("submissions", distinct=True),
            granted_count=Count(
                "submissions",
                filter=Q(submissions__rights_passport__status=UGCRightsPassport.Status.GRANTED),
                distinct=True,
            ),
            drafted_count=Count(
                "submissions",
                filter=Q(submissions__metadata__studio_post_ids__isnull=False),
                distinct=True,
            ),
            latest_content_at=Max("submissions__submitted_at"),
        )
        .order_by("-last_seen_at", "display_name")
    )


def _decorate_creator(creator):
    creator.primary_identity = _primary_identity(creator)
    creator.tag_list = [str(tag) for tag in creator.tags if str(tag).strip()] if isinstance(creator.tags, list) else []
    return creator


@login_required
@require_permission("manage_workspace_settings")
def creator_hub(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    query = str(request.GET.get("q") or "").strip()[:120]
    stage = str(request.GET.get("stage") or "all").strip().lower()
    valid_stages = {value for value, _label in UGCCreator.RelationshipStage.choices}
    if stage not in valid_stages | {"all"}:
        stage = "all"

    creators = _creator_queryset(workspace)
    if stage != "all":
        creators = creators.filter(relationship_stage=stage)
    if query:
        creators = creators.filter(
            Q(display_name__icontains=query)
            | Q(preferred_credit__icontains=query)
            | Q(notes__icontains=query)
            | Q(identities__handle__icontains=query)
            | Q(identities__external_id__icontains=query)
        ).distinct()

    paginator = Paginator(creators, CREATOR_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)
    for creator in page.object_list:
        _decorate_creator(creator)

    all_creators = UGCCreator.objects.for_workspace(workspace.id)
    context = {
        "workspace": workspace,
        "creators": page.object_list,
        "creator_page": page,
        "creator_query": query,
        "creator_stage": stage,
        "creator_stage_choices": UGCCreator.RelationshipStage.choices,
        "creator_counts": {
            "all": all_creators.count(),
            "permissioned": all_creators.filter(relationship_stage=UGCCreator.RelationshipStage.PERMISSIONED).count(),
            "trusted": all_creators.filter(relationship_stage=UGCCreator.RelationshipStage.TRUSTED).count(),
            "partner": all_creators.filter(relationship_stage=UGCCreator.RelationshipStage.PARTNER).count(),
            "do_not_contact": all_creators.filter(
                relationship_stage=UGCCreator.RelationshipStage.DO_NOT_CONTACT
            ).count(),
        },
    }
    return render(request, "ugc/creator_hub.html", context)


@login_required
@require_permission("manage_workspace_settings")
def creator_opportunities(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    queue = str(request.GET.get("queue") or "all").strip().lower()
    if queue not in set(OPPORTUNITY_LABELS) | {"all"}:
        queue = "all"
    query = str(request.GET.get("q") or "").strip()[:120]
    creators = _creator_queryset(workspace).exclude(relationship_stage=UGCCreator.RelationshipStage.DO_NOT_CONTACT)
    if query:
        creators = creators.filter(
            Q(display_name__icontains=query)
            | Q(preferred_credit__icontains=query)
            | Q(notes__icontains=query)
            | Q(identities__handle__icontains=query)
        ).distinct()
    creators = list(creators[:CREATOR_RADAR_LIMIT])

    metadata_by_creator = {creator.id: [] for creator in creators}
    creator_ids = list(metadata_by_creator)
    if creator_ids:
        rows = (
            UGCSubmission.objects.for_workspace(workspace.id)
            .filter(creator_id__in=creator_ids)
            .order_by("-submitted_at")
            .values_list("creator_id", "metadata")[:5000]
        )
        for creator_id, metadata in rows:
            metadata_by_creator.setdefault(creator_id, []).append(metadata)

    opportunities = []
    counts = {key: 0 for key in OPPORTUNITY_LABELS}
    for creator in creators:
        _decorate_creator(creator)
        creator.radar_engagement_score = creator_engagement_score(metadata_by_creator.get(creator.id, []))
        creator.opportunity = classify_creator_opportunity(
            creator,
            engagement_score=creator.radar_engagement_score,
        )
        if creator.opportunity is None:
            continue
        counts[creator.opportunity.key] += 1
        if queue == "all" or creator.opportunity.key == queue:
            opportunities.append(creator)
    opportunities.sort(key=lambda item: (item.opportunity.score, item.last_seen_at), reverse=True)

    paginator = Paginator(opportunities, CREATOR_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page") or 1)
    return render(
        request,
        "ugc/creator_opportunities.html",
        {
            "workspace": workspace,
            "opportunity_creators": page.object_list,
            "opportunity_page": page,
            "opportunity_queue": queue,
            "opportunity_query": query,
            "opportunity_labels": OPPORTUNITY_LABELS,
            "opportunity_counts": {"all": sum(counts.values()), **counts},
            "radar_limit_reached": len(creators) == CREATOR_RADAR_LIMIT,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
def creator_detail(request, workspace_id, creator_id):
    workspace = _get_workspace(request, workspace_id)
    creator = get_object_or_404(
        UGCCreator.objects.for_workspace(workspace.id).prefetch_related("identities"),
        id=creator_id,
    )
    _decorate_creator(creator)
    submissions = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(creator=creator)
        .select_related("media_asset", "rights_passport")
        .order_by("-submitted_at")[:100]
    )
    submission_ids = [str(item.id) for item in submissions]
    events = list(
        AuditEvent.objects.for_workspace(workspace.id)
        .filter(
            Q(target_type="common.ugccreator", target_id=str(creator.id))
            | Q(target_type="common.ugcsubmission", target_id__in=submission_ids)
        )
        .select_related("actor")
        .order_by("-created_at")[:60]
    )
    for event in events:
        event.display_action = event.action.replace("ugc.", "").replace("_", " ").title()
    granted = [
        item
        for item in submissions
        if hasattr(item, "rights_passport") and item.rights_passport.status == UGCRightsPassport.Status.GRANTED
    ]
    drafted_count = sum(1 for item in submissions if (item.metadata or {}).get("studio_post_ids"))
    creator.content_count = len(submissions)
    creator.granted_count = len(granted)
    creator.drafted_count = drafted_count
    creator.latest_content_at = submissions[0].submitted_at if submissions else None
    creator_opportunity = classify_creator_opportunity(
        creator,
        engagement_score=creator_engagement_score([item.metadata for item in submissions]),
    )
    context = {
        "workspace": workspace,
        "creator": creator,
        "creator_submissions": submissions,
        "creator_events": events,
        "creator_opportunity": creator_opportunity,
        "creator_stage_choices": UGCCreator.RelationshipStage.choices,
        "creator_stats": {
            "content": len(submissions),
            "granted": len(granted),
            "drafted": drafted_count,
            "response_rate": round((len(granted) / len(submissions)) * 100) if submissions else 0,
        },
    }
    return render(request, "ugc/creator_detail.html", context)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def promote_creator(request, workspace_id, creator_id):
    workspace = _get_workspace(request, workspace_id)
    creator = get_object_or_404(UGCCreator.objects.for_workspace(workspace.id), id=creator_id)
    target_stage = str(request.POST.get("target_stage") or "").strip().lower()
    allowed_transition = {
        UGCCreator.RelationshipStage.PERMISSIONED: UGCCreator.RelationshipStage.TRUSTED,
        UGCCreator.RelationshipStage.TRUSTED: UGCCreator.RelationshipStage.PARTNER,
    }.get(creator.relationship_stage)
    fallback = reverse("ugc:creator_detail", kwargs={"workspace_id": workspace.id, "creator_id": creator.id})
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    if target_stage != allowed_transition:
        messages.error(request, "That creator relationship promotion is no longer available.")
        return redirect(return_to)

    before = creator.relationship_stage
    creator.relationship_stage = target_stage
    creator.save(update_fields=["relationship_stage", "updated_at"])
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_promoted",
        target=creator,
        metadata={"before": before, "after": target_stage, "source": "opportunity_radar"},
        request=request,
    )
    messages.success(request, f"Creator promoted to {creator.get_relationship_stage_display()}.")
    return redirect(return_to)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_creator(request, workspace_id, creator_id):
    workspace = _get_workspace(request, workspace_id)
    creator = get_object_or_404(UGCCreator.objects.for_workspace(workspace.id), id=creator_id)
    stage = str(request.POST.get("relationship_stage") or "").strip().lower()
    if stage not in {value for value, _label in UGCCreator.RelationshipStage.choices}:
        messages.error(request, "Choose a valid creator relationship stage.")
        return redirect("ugc:creator_detail", workspace_id=workspace.id, creator_id=creator.id)

    old = {
        "display_name": creator.display_name,
        "relationship_stage": creator.relationship_stage,
        "preferred_credit": creator.preferred_credit,
        "tags": creator.tags,
        "notes": creator.notes,
    }
    tags = []
    for raw in str(request.POST.get("tags") or "").split(","):
        tag = raw.strip()[:50]
        if tag and tag.lower() not in {item.lower() for item in tags}:
            tags.append(tag)
        if len(tags) >= 20:
            break
    creator.display_name = str(request.POST.get("display_name") or "").strip()[:255]
    creator.relationship_stage = stage
    creator.preferred_credit = str(request.POST.get("preferred_credit") or "").strip()[:255]
    creator.tags = tags
    creator.notes = str(request.POST.get("notes") or "").strip()[:5000]
    creator.save(
        update_fields=["display_name", "relationship_stage", "preferred_credit", "tags", "notes", "updated_at"]
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_updated",
        target=creator,
        metadata={
            "before": old,
            "after": {
                "display_name": creator.display_name,
                "relationship_stage": creator.relationship_stage,
                "preferred_credit": creator.preferred_credit,
                "tags": creator.tags,
                "notes": creator.notes,
            },
        },
        request=request,
    )
    messages.success(request, "Creator relationship updated.")
    return redirect("ugc:creator_detail", workspace_id=workspace.id, creator_id=creator.id)


def _passport_snapshot(passport):
    return {
        "status": passport.status,
        "allow_organic_social": passport.allow_organic_social,
        "allow_website": passport.allow_website,
        "allow_email": passport.allow_email,
        "allow_paid_ads": passport.allow_paid_ads,
        "allow_print": passport.allow_print,
        "allowed_account_ids": passport.allowed_account_ids,
        "credit_required": passport.credit_required,
        "credit_text": passport.credit_text,
        "evidence_url": passport.evidence_url,
        "evidence_note": passport.evidence_note,
        "consent_version": passport.consent_version,
        "granted_at": passport.granted_at.isoformat() if passport.granted_at else "",
        "expires_at": passport.expires_at.isoformat() if passport.expires_at else "",
        "revoked_at": passport.revoked_at.isoformat() if passport.revoked_at else "",
    }


def _parse_expiry(value):
    value = str(value or "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        if parsed_date:
            parsed = datetime.combine(parsed_date, time.max)
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


@login_required
@require_permission("manage_workspace_settings")
def rights_passport(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(
        UGCSubmission.objects.for_workspace(workspace.id).select_related("creator", "media_asset"),
        id=submission_id,
    )
    passport = sync_rights_passport_from_submission(submission)
    accounts = list(
        SocialAccount.objects.for_workspace(workspace.id)
        .order_by("platform", "account_name")
        .only("id", "platform", "account_name", "account_handle", "connection_status")
    )
    selected_account_ids = {str(value) for value in passport.allowed_account_ids}
    for account in accounts:
        account.rights_selected = str(account.id) in selected_account_ids
    return_to = _safe_local_path(
        request,
        request.GET.get("return_to"),
        reverse("ugc:creator_detail", kwargs={"workspace_id": workspace.id, "creator_id": submission.creator_id})
        if submission.creator_id
        else reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id}),
    )
    return render(
        request,
        "ugc/rights_passport.html",
        {
            "workspace": workspace,
            "submission": submission,
            "passport": passport,
            "rights_status_choices": UGCRightsPassport.Status.choices,
            "social_accounts": accounts,
            "return_to": return_to,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_rights_passport(request, workspace_id, submission_id):
    workspace = _get_workspace(request, workspace_id)
    submission = get_object_or_404(
        UGCSubmission.objects.for_workspace(workspace.id).select_related("creator"),
        id=submission_id,
    )
    passport = sync_rights_passport_from_submission(submission)
    status = str(request.POST.get("status") or "").strip().lower()
    if status not in {value for value, _label in UGCRightsPassport.Status.choices}:
        messages.error(request, "Choose a valid rights status.")
        return redirect("ugc:rights_passport", workspace_id=workspace.id, submission_id=submission.id)
    expires_at = _parse_expiry(request.POST.get("expires_at"))
    if request.POST.get("expires_at") and expires_at is None:
        messages.error(request, "Enter a valid expiration date.")
        return redirect("ugc:rights_passport", workspace_id=workspace.id, submission_id=submission.id)
    if status == UGCRightsPassport.Status.GRANTED and expires_at and expires_at <= timezone.now():
        messages.error(request, "Granted rights cannot already be expired.")
        return redirect("ugc:rights_passport", workspace_id=workspace.id, submission_id=submission.id)
    evidence_url = str(request.POST.get("evidence_url") or "").strip()[:2000]
    if evidence_url:
        try:
            URLValidator(schemes=["http", "https"])(evidence_url)
        except ValidationError:
            messages.error(request, "Enter a valid http or https evidence URL.")
            return redirect("ugc:rights_passport", workspace_id=workspace.id, submission_id=submission.id)

    before = _passport_snapshot(passport)
    now = timezone.now()
    passport.status = status
    passport.allow_organic_social = request.POST.get("allow_organic_social") == "on"
    passport.allow_website = request.POST.get("allow_website") == "on"
    passport.allow_email = request.POST.get("allow_email") == "on"
    passport.allow_paid_ads = request.POST.get("allow_paid_ads") == "on"
    passport.allow_print = request.POST.get("allow_print") == "on"
    valid_account_ids = {
        str(value)
        for value in SocialAccount.objects.for_workspace(workspace.id)
        .filter(id__in=request.POST.getlist("allowed_account_ids"))
        .values_list("id", flat=True)
    }
    passport.allowed_account_ids = sorted(valid_account_ids)
    passport.credit_required = request.POST.get("credit_required") == "on"
    passport.credit_text = str(request.POST.get("credit_text") or "").strip()[:500]
    passport.evidence_url = evidence_url
    passport.evidence_note = str(request.POST.get("evidence_note") or "").strip()[:5000]
    passport.consent_version = str(request.POST.get("consent_version") or "").strip()[:50]
    passport.expires_at = expires_at
    passport.recorded_by = request.user
    if status == UGCRightsPassport.Status.GRANTED:
        passport.granted_at = passport.granted_at or now
        passport.revoked_at = None
    elif status == UGCRightsPassport.Status.REVOKED:
        passport.revoked_at = now
    elif status == UGCRightsPassport.Status.EXPIRED:
        passport.expires_at = passport.expires_at or now
    passport.save()

    if status == UGCRightsPassport.Status.GRANTED:
        submission.consent_confirmed = True
        submission.consent_version = passport.consent_version or "creator-permission-v1"
        submission.consent_at = passport.granted_at or now
        submission.metadata = set_permission(
            submission.metadata,
            status=GRANTED,
            channel="rights_passport",
            note="Rights passport recorded.",
            updated_at=now.isoformat(),
        )
    elif status in {
        UGCRightsPassport.Status.NOT_REQUESTED,
        UGCRightsPassport.Status.REQUESTED,
        UGCRightsPassport.Status.DECLINED,
    }:
        permission_status = {
            UGCRightsPassport.Status.NOT_REQUESTED: NOT_CONTACTED,
            UGCRightsPassport.Status.REQUESTED: REQUESTED,
            UGCRightsPassport.Status.DECLINED: DECLINED,
        }[status]
        submission.consent_confirmed = False
        submission.metadata = set_permission(
            submission.metadata,
            status=permission_status,
            channel="rights_passport",
            note="Rights passport updated.",
            updated_at=now.isoformat(),
        )
    elif status in {
        UGCRightsPassport.Status.REVOKED,
        UGCRightsPassport.Status.EXPIRED,
    }:
        submission.consent_confirmed = False
    submission.save(update_fields=["consent_confirmed", "consent_version", "consent_at", "metadata", "updated_at"])

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.rights_passport_updated",
        target=submission,
        metadata={"before": before, "after": _passport_snapshot(passport)},
        request=request,
    )
    messages.success(request, "Rights passport updated.")
    fallback = reverse("ugc:rights_passport", kwargs={"workspace_id": workspace.id, "submission_id": submission.id})
    return redirect(_safe_local_path(request, request.POST.get("return_to"), fallback))
