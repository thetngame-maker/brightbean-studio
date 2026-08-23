"""Server-rendered creator collaboration briefs and status actions."""

from datetime import datetime, time, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import (
    UGCCreator,
    UGCCreatorCollaboration,
    UGCCreatorCollaborationInvite,
    UGCCreatorIdentity,
    UGCCreatorTask,
    UGCSubmission,
)
from .ugc_creator_collaboration_invites import close_pending_collaboration_invites, expire_collaboration_invite
from .ugc_creator_collaboration_milestones import collaboration_milestone_summary
from .ugc_creator_services import rights_can_use
from .ugc_creator_views import _decorate_creator, _get_workspace, _safe_local_path
from .ugc_target_catalog import find_catalog_target, target_choices

COLLABORATION_PAGE_SIZE = 12
ACTIVE_STATUSES = {
    UGCCreatorCollaboration.Status.DRAFT,
    UGCCreatorCollaboration.Status.INVITED,
    UGCCreatorCollaboration.Status.INTERESTED,
    UGCCreatorCollaboration.Status.CONFIRMED,
    UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
}
FILTER_STATUSES = {
    "drafts": {UGCCreatorCollaboration.Status.DRAFT},
    "awaiting": {
        UGCCreatorCollaboration.Status.INVITED,
        UGCCreatorCollaboration.Status.INTERESTED,
    },
    "confirmed": {
        UGCCreatorCollaboration.Status.CONFIRMED,
        UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
    },
    "completed": {UGCCreatorCollaboration.Status.COMPLETED},
    "closed": {
        UGCCreatorCollaboration.Status.DECLINED,
        UGCCreatorCollaboration.Status.CANCELLED,
    },
}
RIGHTS_CHOICES = (
    ("organic_social", "Organic social"),
    ("website", "Website"),
    ("email", "Email"),
    ("paid_ads", "Paid ads"),
    ("print", "Print"),
)
RIGHTS_LABELS = dict(RIGHTS_CHOICES)


def _parse_due_at(value):
    value = str(value or "").strip()
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed is None:
        parsed_date = parse_date(value)
        parsed = datetime.combine(parsed_date, time(hour=17)) if parsed_date else None
    if parsed and timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _requested_rights(request):
    valid = set(RIGHTS_LABELS)
    return [value for value in request.POST.getlist("requested_rights") if value in valid]


def _target_from_request(workspace, request):
    target_key = str(request.POST.get("target_key") or "").strip()
    if not target_key:
        return {"target_type": "", "target_id": "", "target_label": "", "target_url": ""}
    if "::" not in target_key:
        return None
    target_type, target_id = target_key.split("::", 1)
    return find_catalog_target(workspace, target_type, target_id)


def _creator_name(creator):
    identity = next((item for item in creator.identities.all() if item.is_primary), None)
    handle = identity.handle if identity else ""
    return creator.display_name or (f"@{handle}" if handle else "there")


def _default_invite_message(creator, *, title, target_label, deliverables, offer, requested_rights):
    target_phrase = f" at {target_label}" if target_label else ""
    deliverable_text = deliverables.strip().rstrip(".")
    deliverable_phrase = f" We’re looking for {deliverable_text}." if deliverable_text else ""
    offer_phrase = f" We can offer {offer.strip()}." if offer.strip() else ""
    rights = [RIGHTS_LABELS[value].lower() for value in requested_rights if value in RIGHTS_LABELS]
    rights_phrase = f" We’d request permission to use the final content for {', '.join(rights)}, with credit."
    if not rights:
        rights_phrase = " We’ll confirm usage rights with you before anything is published."
    return (
        f"Hi {_creator_name(creator)}! We’d love to collaborate with you on {title.strip()}{target_phrase}."
        f"{deliverable_phrase}{offer_phrase}{rights_phrase} Interested?"
    )[:5000]


def _collaboration_rights_can_complete(collaboration):
    if not collaboration.submission_id:
        return False, "No delivered content is linked yet."
    scopes = collaboration.requested_rights or ["organic_social"]
    for scope in scopes:
        allowed, reason = rights_can_use(collaboration.submission, scope=scope)
        if not allowed:
            return False, reason
    return True, "All requested usage scopes are active on the linked Rights Passport."


def _complete_collaboration_tasks(collaboration, actor):
    now = timezone.now()
    UGCCreatorTask.objects.filter(
        collaboration=collaboration,
        status=UGCCreatorTask.Status.OPEN,
    ).update(
        status=UGCCreatorTask.Status.DONE,
        completed_at=now,
        completed_by=actor,
        updated_at=now,
    )


def _next_collaboration_task(collaboration, actor, *, title, due_at, note=""):
    _complete_collaboration_tasks(collaboration, actor)
    return UGCCreatorTask.objects.create(
        workspace=collaboration.workspace,
        creator=collaboration.creator,
        collaboration=collaboration,
        kind=UGCCreatorTask.Kind.COLLABORATION,
        title=title[:255],
        note=note[:5000],
        due_at=due_at,
        created_by=actor,
    )


def _decorate_collaboration(collaboration):
    _decorate_creator(collaboration.creator)
    collaboration.requested_rights_labels = [
        RIGHTS_LABELS[value] for value in collaboration.requested_rights if value in RIGHTS_LABELS
    ]
    collaboration.milestones = collaboration_milestone_summary(collaboration)
    return collaboration


@login_required
@require_permission("manage_workspace_settings")
def creator_collaborations(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    queue = str(request.GET.get("view") or "active").strip().lower()
    if queue not in {"active", *FILTER_STATUSES}:
        queue = "active"
    query = str(request.GET.get("q") or "").strip()[:120]
    identities = UGCCreatorIdentity.objects.order_by("-is_primary", "platform", "normalized_handle")
    collaborations = (
        UGCCreatorCollaboration.objects.for_workspace(workspace.id)
        .select_related("creator", "submission", "submission__rights_passport")
        .prefetch_related(Prefetch("creator__identities", queryset=identities))
    )
    statuses = ACTIVE_STATUSES if queue == "active" else FILTER_STATUSES[queue]
    collaborations = collaborations.filter(status__in=statuses)
    if query:
        collaborations = collaborations.filter(
            Q(title__icontains=query)
            | Q(brief__icontains=query)
            | Q(target_label__icontains=query)
            | Q(creator__display_name__icontains=query)
            | Q(creator__identities__handle__icontains=query)
        ).distinct()
    collaborations = collaborations.order_by("content_due_at", "-updated_at")
    page = Paginator(collaborations, COLLABORATION_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    for collaboration in page.object_list:
        _decorate_collaboration(collaboration)

    all_collaborations = UGCCreatorCollaboration.objects.for_workspace(workspace.id)
    counts = {
        "active": all_collaborations.filter(status__in=ACTIVE_STATUSES).count(),
        **{name: all_collaborations.filter(status__in=statuses).count() for name, statuses in FILTER_STATUSES.items()},
    }
    return render(
        request,
        "ugc/creator_collaborations.html",
        {
            "workspace": workspace,
            "collaborations": page.object_list,
            "collaboration_page": page,
            "collaboration_queue": queue,
            "collaboration_query": query,
            "collaboration_counts": counts,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
def creator_collaboration_detail(request, workspace_id, collaboration_id):
    workspace = _get_workspace(request, workspace_id)
    collaboration = get_object_or_404(
        UGCCreatorCollaboration.objects.for_workspace(workspace.id)
        .select_related("creator", "submission", "submission__rights_passport")
        .prefetch_related("creator__identities"),
        id=collaboration_id,
    )
    _decorate_collaboration(collaboration)
    creator_submissions = list(
        UGCSubmission.objects.for_workspace(workspace.id)
        .filter(creator=collaboration.creator)
        .select_related("rights_passport")
        .order_by("-submitted_at")[:100]
    )
    rights_ready, rights_reason = _collaboration_rights_can_complete(collaboration)
    creator_invites = list(collaboration.creator_invites.select_related("created_by").order_by("-created_at")[:8])
    for invite in creator_invites:
        expire_collaboration_invite(invite)
        invite.public_url = ""
        if invite.is_available:
            invite.public_url = request.build_absolute_uri(
                reverse("creator_collaboration_public:respond", kwargs={"token": invite.request_token})
            )
    return render(
        request,
        "ugc/creator_collaboration_detail.html",
        {
            "workspace": workspace,
            "collaboration": collaboration,
            "creator_submissions": creator_submissions,
            "target_choices": target_choices(workspace, limit=80),
            "rights_choices": RIGHTS_CHOICES,
            "rights_ready": rights_ready,
            "rights_reason": rights_reason,
            "creator_invites": creator_invites,
            "active_creator_invite": next((item for item in creator_invites if item.is_available), None),
            "can_create_creator_invite": collaboration.status
            in {
                UGCCreatorCollaboration.Status.DRAFT,
                UGCCreatorCollaboration.Status.INVITED,
                UGCCreatorCollaboration.Status.INTERESTED,
            }
            and collaboration.creator.relationship_stage != UGCCreator.RelationshipStage.DO_NOT_CONTACT,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_creator_collaboration(request, workspace_id, creator_id):
    workspace = _get_workspace(request, workspace_id)
    creator = get_object_or_404(
        UGCCreator.objects.for_workspace(workspace.id).prefetch_related("identities"),
        id=creator_id,
    )
    fallback = reverse("ugc:creator_detail", kwargs={"workspace_id": workspace.id, "creator_id": creator.id})
    if creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT:
        messages.error(request, "This creator is marked Do not contact. Update the relationship before inviting them.")
        return redirect(_safe_local_path(request, request.POST.get("return_to"), fallback))
    title = str(request.POST.get("title") or "").strip()[:255]
    deliverables = str(request.POST.get("deliverables") or "").strip()[:5000]
    if not title or not deliverables:
        messages.error(request, "Add a collaboration title and the expected deliverables.")
        return redirect(_safe_local_path(request, request.POST.get("return_to"), fallback))
    target = _target_from_request(workspace, request)
    if target is None:
        messages.error(request, "Choose a TN Game target from the existing target catalog.")
        return redirect(_safe_local_path(request, request.POST.get("return_to"), fallback))
    due_at = _parse_due_at(request.POST.get("content_due_at"))
    rights = _requested_rights(request)
    offer = str(request.POST.get("offer") or "").strip()[:500]
    invite_message = str(request.POST.get("invite_message") or "").strip()[:5000]
    if not invite_message:
        invite_message = _default_invite_message(
            creator,
            title=title,
            target_label=target.get("target_label") or "",
            deliverables=deliverables,
            offer=offer,
            requested_rights=rights,
        )
    collaboration = UGCCreatorCollaboration.objects.create(
        workspace=workspace,
        creator=creator,
        title=title,
        brief=str(request.POST.get("brief") or "").strip()[:5000],
        deliverables=deliverables,
        offer=offer,
        target_type=target.get("target_type") or "",
        target_id=target.get("target_id") or "",
        target_label=target.get("target_label") or "",
        target_url=target.get("target_url") or "",
        requested_rights=rights,
        invite_message=invite_message,
        content_due_at=due_at,
        created_by=request.user,
    )
    _next_collaboration_task(
        collaboration,
        request.user,
        title=f"Send collaboration invite · {title}",
        due_at=timezone.now(),
        note=target.get("target_label") or "",
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.creator_collaboration_created",
        target=creator,
        metadata={
            "collaboration_id": str(collaboration.id),
            "title": collaboration.title,
            "target_type": collaboration.target_type,
            "target_id": collaboration.target_id,
            "requested_rights": collaboration.requested_rights,
        },
        request=request,
    )
    messages.success(request, "Collaboration brief created. Copy the invitation when you are ready to send it.")
    return redirect(
        "ugc:creator_collaboration_detail",
        workspace_id=workspace.id,
        collaboration_id=collaboration.id,
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_creator_collaboration(request, workspace_id, collaboration_id):
    workspace = _get_workspace(request, workspace_id)
    collaboration = get_object_or_404(
        UGCCreatorCollaboration.objects.for_workspace(workspace.id)
        .select_related("creator", "submission")
        .prefetch_related("creator__identities"),
        id=collaboration_id,
    )
    fallback = reverse(
        "ugc:creator_collaboration_detail",
        kwargs={"workspace_id": workspace.id, "collaboration_id": collaboration.id},
    )
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    action = str(request.POST.get("action") or "").strip().lower()
    before = {"status": collaboration.status, "submission_id": str(collaboration.submission_id or "")}

    if action == "save":
        target = _target_from_request(workspace, request)
        if target is None:
            messages.error(request, "Choose a TN Game target from the existing target catalog.")
            return redirect(return_to)
        title = str(request.POST.get("title") or "").strip()[:255]
        deliverables = str(request.POST.get("deliverables") or "").strip()[:5000]
        if not title or not deliverables:
            messages.error(request, "Add a collaboration title and the expected deliverables.")
            return redirect(return_to)
        collaboration.title = title
        collaboration.brief = str(request.POST.get("brief") or "").strip()[:5000]
        collaboration.deliverables = deliverables
        collaboration.offer = str(request.POST.get("offer") or "").strip()[:500]
        collaboration.target_type = target.get("target_type") or ""
        collaboration.target_id = target.get("target_id") or ""
        collaboration.target_label = target.get("target_label") or ""
        collaboration.target_url = target.get("target_url") or ""
        collaboration.requested_rights = _requested_rights(request)
        invite_message = str(request.POST.get("invite_message") or "").strip()[:5000]
        collaboration.invite_message = invite_message or _default_invite_message(
            collaboration.creator,
            title=title,
            target_label=collaboration.target_label,
            deliverables=deliverables,
            offer=collaboration.offer,
            requested_rights=collaboration.requested_rights,
        )
        collaboration.content_due_at = _parse_due_at(request.POST.get("content_due_at"))
        collaboration.save()
        superseded_invites = close_pending_collaboration_invites(
            collaboration,
            status=UGCCreatorCollaborationInvite.Status.SUPERSEDED,
        )
        message = "Collaboration brief updated."
        if superseded_invites:
            message += " The previous creator link was closed because its terms changed."
    elif action == "link_content":
        submission_id = str(request.POST.get("submission_id") or "").strip()
        submission = None
        if submission_id:
            submission = get_object_or_404(
                UGCSubmission.objects.for_workspace(workspace.id),
                id=submission_id,
                creator=collaboration.creator,
            )
        collaboration.submission = submission
        collaboration.save(update_fields=["submission", "updated_at"])
        message = "Delivered content linked." if submission else "Delivered content link removed."
    else:
        transition = {
            "mark_invited": ({UGCCreatorCollaboration.Status.DRAFT}, UGCCreatorCollaboration.Status.INVITED),
            "mark_interested": (
                {UGCCreatorCollaboration.Status.INVITED},
                UGCCreatorCollaboration.Status.INTERESTED,
            ),
            "mark_confirmed": (
                {UGCCreatorCollaboration.Status.INVITED, UGCCreatorCollaboration.Status.INTERESTED},
                UGCCreatorCollaboration.Status.CONFIRMED,
            ),
            "mark_received": (
                {UGCCreatorCollaboration.Status.CONFIRMED},
                UGCCreatorCollaboration.Status.CONTENT_RECEIVED,
            ),
            "mark_completed": (
                {UGCCreatorCollaboration.Status.CONTENT_RECEIVED},
                UGCCreatorCollaboration.Status.COMPLETED,
            ),
            "mark_declined": (
                {
                    UGCCreatorCollaboration.Status.INVITED,
                    UGCCreatorCollaboration.Status.INTERESTED,
                    UGCCreatorCollaboration.Status.CONFIRMED,
                },
                UGCCreatorCollaboration.Status.DECLINED,
            ),
            "cancel": (ACTIVE_STATUSES, UGCCreatorCollaboration.Status.CANCELLED),
            "reopen": (
                {UGCCreatorCollaboration.Status.DECLINED, UGCCreatorCollaboration.Status.CANCELLED},
                UGCCreatorCollaboration.Status.DRAFT,
            ),
        }.get(action)
        if transition is None or collaboration.status not in transition[0]:
            messages.error(request, "That collaboration action is no longer available.")
            return redirect(return_to)
        if (
            action == "mark_invited"
            and collaboration.creator.relationship_stage == UGCCreator.RelationshipStage.DO_NOT_CONTACT
        ):
            messages.error(request, "This creator is marked Do not contact. The invitation was not logged as sent.")
            return redirect(return_to)
        if action == "mark_completed":
            if not collaboration.submission_id:
                messages.error(request, "Link the delivered content before completing this collaboration.")
                return redirect(return_to)
            allowed, reason = _collaboration_rights_can_complete(collaboration)
            if not allowed:
                messages.error(request, f"Usage rights are not ready: {reason}")
                return redirect(return_to)

        collaboration.status = transition[1]
        now = timezone.now()
        if collaboration.status == UGCCreatorCollaboration.Status.INVITED:
            collaboration.invited_at = now
            collaboration.creator.last_contacted_at = now
            if collaboration.creator.relationship_stage == UGCCreator.RelationshipStage.PROSPECT:
                collaboration.creator.relationship_stage = UGCCreator.RelationshipStage.CONTACTED
            collaboration.creator.save(update_fields=["last_contacted_at", "relationship_stage", "updated_at"])
            _next_collaboration_task(
                collaboration,
                request.user,
                title=f"Follow up on collaboration · {collaboration.title}",
                due_at=now + timedelta(days=3),
            )
        elif collaboration.status == UGCCreatorCollaboration.Status.INTERESTED:
            _next_collaboration_task(
                collaboration,
                request.user,
                title=f"Confirm collaboration details · {collaboration.title}",
                due_at=now,
            )
        elif collaboration.status == UGCCreatorCollaboration.Status.CONFIRMED:
            _next_collaboration_task(
                collaboration,
                request.user,
                title=f"Check in on deliverables · {collaboration.title}",
                due_at=collaboration.content_due_at or now + timedelta(days=7),
            )
        elif collaboration.status == UGCCreatorCollaboration.Status.CONTENT_RECEIVED:
            _next_collaboration_task(
                collaboration,
                request.user,
                title=f"Link content and verify rights · {collaboration.title}",
                due_at=now,
            )
        elif collaboration.status == UGCCreatorCollaboration.Status.DRAFT:
            _next_collaboration_task(
                collaboration,
                request.user,
                title=f"Send collaboration invite · {collaboration.title}",
                due_at=now,
            )
        else:
            _complete_collaboration_tasks(collaboration, request.user)
        collaboration.completed_at = now if collaboration.status == UGCCreatorCollaboration.Status.COMPLETED else None
        collaboration.save(update_fields=["status", "invited_at", "completed_at", "updated_at"])
        if collaboration.status not in {
            UGCCreatorCollaboration.Status.DRAFT,
            UGCCreatorCollaboration.Status.INVITED,
            UGCCreatorCollaboration.Status.INTERESTED,
        }:
            close_pending_collaboration_invites(collaboration)
        message = f"Collaboration moved to {collaboration.get_status_display()}."

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=f"ugc.creator_collaboration_{action}",
        target=collaboration.creator,
        metadata={
            "collaboration_id": str(collaboration.id),
            "title": collaboration.title,
            "before": before,
            "after": {
                "status": collaboration.status,
                "submission_id": str(collaboration.submission_id or ""),
            },
        },
        request=request,
    )
    messages.success(request, message)
    return redirect(return_to)
