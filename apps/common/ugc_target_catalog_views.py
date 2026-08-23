"""Mobile-friendly TN Game target catalog for Community workflows."""

from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from apps.members.decorators import require_permission

from .ugc_target_catalog import build_target_catalog
from .ugc_views import _get_workspace


@login_required
@require_permission("manage_workspace_settings")
def target_catalog(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    query = (request.GET.get("q") or "").strip().lower()
    target_type = (request.GET.get("type") or "").strip().lower()

    targets = build_target_catalog(workspace, limit=500)
    types = sorted({item["target_type"] for item in targets})

    if target_type:
        targets = [item for item in targets if item["target_type"] == target_type]
    if query:
        targets = [
            item for item in targets
            if query in item["target_label"].lower()
            or query in item["target_id"].lower()
            or any(query in alias.lower() for alias in item.get("aliases", []))
        ]

    return render(
        request,
        "ugc/target_catalog_mobile.html",
        {
            "workspace": workspace,
            "targets": targets,
            "target_catalog_query": request.GET.get("q", ""),
            "target_catalog_type": target_type,
            "target_catalog_types": types,
            "target_catalog_total": len(build_target_catalog(workspace, limit=500)),
        },
    )
