"""Staff review actions for creator-submitted collaboration deliveries."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .models import UGCCreatorCollaborationDelivery
from .ugc_creator_collaboration_deliveries import (
    CreatorDeliveryError,
    replace_delivery_rights_request,
    review_creator_delivery,
)
from .ugc_creator_views import _get_workspace, _safe_local_path


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def review_creator_delivery_view(request, workspace_id, delivery_id):
    workspace = _get_workspace(request, workspace_id)
    delivery = get_object_or_404(
        UGCCreatorCollaborationDelivery.objects.for_workspace(workspace.id).select_related("collaboration"),
        id=delivery_id,
    )
    fallback = reverse(
        "ugc:creator_collaboration_detail",
        kwargs={"workspace_id": workspace.id, "collaboration_id": delivery.collaboration_id},
    )
    return_to = _safe_local_path(request, request.POST.get("return_to"), fallback)
    action = str(request.POST.get("action") or "").strip().lower()
    try:
        if action == "refresh_rights":
            replace_delivery_rights_request(delivery, actor=request.user)
            messages.success(request, "A replacement secure Rights Passport request is ready to send.")
            return redirect(return_to)
        delivery, rights_request = review_creator_delivery(
            delivery,
            action=action,
            review_note=request.POST.get("review_note"),
            actor=request.user,
        )
    except CreatorDeliveryError as exc:
        messages.error(request, str(exc))
        return redirect(return_to)
    if action == "accept":
        message = "Creator delivery accepted."
        if rights_request:
            message += " The secure Rights Passport request is ready to send."
        else:
            message += " Choose usage rights before publishing this content."
        messages.success(request, message)
    else:
        messages.success(request, f"Revision feedback sent for delivery {delivery.revision_number}.")
    return redirect(return_to)
