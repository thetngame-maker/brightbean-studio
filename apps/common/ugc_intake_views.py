"""Small moderator-facing intake form for manual/end-to-end UGC testing."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.members.decorators import require_permission

from .models import UGCSubmission
from .ugc_views import _get_workspace


@login_required
@require_permission("manage_workspace_settings")
def manual_submission_form(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    return render(
        request,
        "ugc/manual_submission_form.html",
        {
            "workspace": workspace,
            "kind_choices": UGCSubmission.Kind.choices,
            "attribution_choices": UGCSubmission.Attribution.choices,
        },
    )
