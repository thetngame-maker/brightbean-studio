"""Workspace controls for Smart Grant and Smart Remove keyword rules."""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission

from .audit import record_audit_event
from .ugc_smart_selection import normalize_smart_rules
from .ugc_views import _get_workspace

MAX_KEYWORDS_PER_LIST = 80


def _keywords(raw):
    values = re.split(r"[,\n\r]+", str(raw or ""))
    return values[:MAX_KEYWORDS_PER_LIST]


def _safe_return_to(request, workspace):
    return_to = (request.POST.get("return_to") or "").strip()
    if return_to.startswith("/") and not return_to.startswith("//"):
        return redirect(return_to)
    return redirect("ugc:moderation_queue", workspace_id=workspace.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_smart_rules(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    if request.POST.get("action") == "reset":
        rules = {}
        message = "Smart selection keywords reset to the Tennessee defaults."
    else:
        rules = normalize_smart_rules(
            _keywords(request.POST.get("grant_keywords")),
            _keywords(request.POST.get("remove_keywords")),
        )
        message = "Smart selection keywords saved."

    workspace.community_smart_rules = rules
    workspace.save(update_fields=["community_smart_rules", "updated_at"])
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.smart_selection_rules_updated",
        target=workspace,
        target_label=workspace.name,
        metadata={
            "reset": not bool(rules),
            "grant_keyword_count": len(rules.get("grant", [])),
            "remove_keyword_count": len(rules.get("remove", [])),
        },
        request=request,
    )
    messages.success(request, message)
    return _safe_return_to(request, workspace)
