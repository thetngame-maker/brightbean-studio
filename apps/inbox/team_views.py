from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership

from . import views
from .forms import BulkActionForm
from .models import InboxMessage, InboxSLAConfig


def _assign_work_item(message, assigned_to):
    """Assign one visible inbox work item, including every row in a DM thread."""
    if message.message_type == InboxMessage.MessageType.DM and message.sender_handle:
        InboxMessage.objects.filter(
            workspace=message.workspace,
            social_account=message.social_account,
            message_type=InboxMessage.MessageType.DM,
            sender_handle=message.sender_handle,
        ).update(assigned_to=assigned_to)
        return

    InboxMessage.objects.filter(pk=message.pk).update(assigned_to=assigned_to)


@login_required
@require_permission("reply_from_inbox")
@require_POST
def bulk_assign(request, workspace_id):
    """Assign or unassign selected inbox work items as complete conversations."""
    workspace = views._get_workspace(request, workspace_id)
    form = BulkActionForm(request.POST)
    if not form.is_valid() or form.cleaned_data.get("action") != "assign":
        return HttpResponse("Invalid bulk assignment.", status=400)

    assigned_to = None
    value = form.cleaned_data.get("value", "").strip()
    if value:
        membership = get_object_or_404(
            WorkspaceMembership.objects.select_related("user"),
            workspace=workspace,
            user_id=value,
        )
        assigned_to = membership.user

    selected = list(
        InboxMessage.objects.filter(
            workspace=workspace,
            id__in=form.cleaned_data["message_ids"],
        ).select_related("social_account")
    )
    for message in selected:
        _assign_work_item(message, assigned_to)

    messages = InboxMessage.objects.for_workspace(workspace.id).select_related(
        "social_account", "assigned_to"
    )[: views.MESSAGES_PER_PAGE]
    sla_config = InboxSLAConfig.objects.filter(workspace=workspace, is_active=True).first()
    return render(
        request,
        "inbox/partials/_message_list.html",
        {
            "workspace": workspace,
            "inbox_messages": messages,
            "sla_config": sla_config,
        },
    )
