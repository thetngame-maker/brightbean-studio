from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from . import views
from .collaboration import ACTIVITY_PREFIX
from .forms import InternalNoteForm
from .models import InternalNote


def _editable_note(request, workspace_id, note_id):
    workspace = views._get_workspace(request, workspace_id)
    note = get_object_or_404(
        InternalNote.objects.select_related("inbox_message", "author"),
        id=note_id,
        inbox_message__workspace=workspace,
    )
    if (note.body or "").startswith(ACTIVITY_PREFIX):
        return workspace, note, HttpResponse("Activity history cannot be edited.", status=400)
    if note.author_id != request.user.id:
        return workspace, note, HttpResponse("You can only edit your own internal notes.", status=403)
    return workspace, note, None


@login_required
@require_permission("reply_from_inbox")
@require_POST
def edit_note(request, workspace_id, note_id):
    """Edit an internal note authored by the current teammate."""
    workspace, note, error = _editable_note(request, workspace_id, note_id)
    if error:
        return error

    form = InternalNoteForm(request.POST)
    if not form.is_valid():
        return HttpResponse("Invalid note.", status=400)

    note.body = form.cleaned_data["body"]
    note.save(update_fields=["body"])
    return render(
        request,
        "inbox/partials/_note_item.html",
        {"note": note, "workspace": workspace, "message": note.inbox_message},
    )


@login_required
@require_permission("reply_from_inbox")
@require_POST
def delete_note(request, workspace_id, note_id):
    """Delete an internal note authored by the current teammate."""
    _workspace, note, error = _editable_note(request, workspace_id, note_id)
    if error:
        return error
    note.delete()
    return HttpResponse("")
