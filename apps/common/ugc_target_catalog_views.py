"""Mobile-friendly TN Game target catalog for Community workflows."""

from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from apps.members.decorators import require_permission

from .models import UGCSubmission
from .ugc_discovery_search_views import get_saved_search
from .ugc_discovery_views import TARGET_CHOICES
from .ugc_mobile_quality import decorate_approved_quality
from .ugc_target_catalog import build_target_catalog, target_choices
from .ugc_views import _get_workspace


def _local_path(request, value, fallback):
    value = (value or "").strip()
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


def _catalog_url(params, *, query="", target_type=""):
    values = dict(params)
    if query:
        values["q"] = query
    if target_type:
        values["type"] = target_type
    encoded = urlencode(values)
    return f"?{encoded}" if encoded else "?"


@login_required
@require_permission("manage_workspace_settings")
def target_catalog(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    raw_query = (request.GET.get("q") or "").strip()
    query = raw_query.lower()
    target_type = (request.GET.get("type") or "").strip().lower()

    queue_url = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id})
    discovery_url = reverse("ugc:discovery_searches", kwargs={"workspace_id": workspace.id})
    default_back = f"{queue_url}?tab=approved&draft_state=check"
    selection_mode = ""
    selection_submission = None
    selection_search = None
    selection_params = {}

    submission_id = (request.GET.get("submission") or "").strip()
    search_id = (request.GET.get("search_id") or "").strip()
    if submission_id and search_id:
        raise Http404("Choose one target selection workflow.")

    if submission_id:
        selection_submission = get_object_or_404(
            UGCSubmission,
            id=submission_id,
            workspace=workspace,
            status=UGCSubmission.Status.APPROVED,
        )
        decorate_approved_quality(selection_submission)
        selection_mode = "submission"
        review_url = reverse(
            "ugc:mobile_review",
            kwargs={"workspace_id": workspace.id, "submission_id": selection_submission.id},
        )
        default_back = f"{review_url}?tab=approved&draft_state=check"
        targets = target_choices(
            workspace,
            suggested_label=selection_submission.mobile_suggested_target_label,
            current_submission=selection_submission,
            limit=500,
        )
        selection_params["submission"] = str(selection_submission.id)
    elif search_id:
        selection_search = get_saved_search(workspace, search_id)
        if selection_search is None:
            raise Http404("Discovery search not found.")
        selection_mode = "discovery_search"
        default_back = discovery_url
        targets = build_target_catalog(workspace, limit=500)
        current_key = (selection_search.get("target_type"), selection_search.get("target_id"))
        for item in targets:
            item["is_current"] = (item["target_type"], item["target_id"]) == current_key
            item["is_suggested"] = False
        selection_params["search_id"] = str(selection_search["id"])
    else:
        targets = build_target_catalog(workspace, limit=500)

    back_to = _local_path(request, request.GET.get("back_to"), default_back)
    return_to = _local_path(
        request,
        request.GET.get("return_to"),
        discovery_url if selection_mode == "discovery_search" else f"{queue_url}?tab=approved&draft_state=check",
    )
    if selection_mode:
        selection_params["back_to"] = back_to
        selection_params["return_to"] = return_to

    total_targets = len(targets)
    types = sorted({item["target_type"] for item in targets})

    if target_type:
        targets = [item for item in targets if item["target_type"] == target_type]
    if query:
        targets = [
            item
            for item in targets
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
            "target_catalog_query": raw_query,
            "target_catalog_type": target_type,
            "target_catalog_types": [
                {
                    "value": value,
                    "url": _catalog_url(selection_params, query=raw_query, target_type=value),
                }
                for value in types
            ],
            "target_catalog_all_url": _catalog_url(selection_params, query=raw_query),
            "target_catalog_total": total_targets,
            "target_catalog_selection_mode": selection_mode,
            "target_catalog_submission": selection_submission,
            "target_catalog_search": selection_search,
            "target_catalog_selection_params": selection_params,
            "target_catalog_back_to": back_to,
            "target_catalog_return_to": return_to,
            "target_type_choices": TARGET_CHOICES,
        },
    )
