"""Server-rendered Five-Minute Mobile Command Center."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.common.ugc_views import _get_workspace
from apps.members.decorators import require_permission

from .command_center import build_command_center


@login_required
@require_permission("manage_workspace_settings")
def command_center(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    membership = request.workspace_membership
    permissions = membership.effective_permissions if membership else {}
    briefing = build_command_center(workspace, permissions=permissions)
    response = render(
        request,
        "composer/command_center.html",
        {"workspace": workspace, "command_center": briefing},
    )
    response["X-Studio-Command-Center"] = "1"
    return response
