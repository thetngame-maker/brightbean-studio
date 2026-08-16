"""Moderator-facing simulator for externally discovered community content.

This intentionally mirrors the normalized shape a future discovery provider
will write. It lets Studio exercise discovery -> permission -> moderation
without depending on an external scraper during development.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.media_library.models import MediaAsset
from apps.media_library.services import create_asset
from apps.media_library.tasks import process_media_asset
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import UGCSubmission
from .ugc_permissions import NOT_CONTACTED, set_permission
from .ugc_provenance import build_provenance, set_provenance
from .ugc_views import _get_workspace


PLATFORM_CHOICES = [
    ("instagram", "Instagram"),
    ("facebook", "Facebook"),
    ("tiktok", "TikTok"),
    ("website", "Website"),
    ("other", "Other source"),
]

TARGET_CHOICES = [
    ("top_sight", "Top Sight"),
    ("trail", "Trail"),
    ("checkpoint", "Checkpoint"),
    ("activity", "Activity"),
    ("event", "Event"),
    ("restaurant", "Restaurant"),
    ("lodging", "Lodging"),
    ("community_post", "Community post"),
    ("other", "Other"),
]


@login_required
@require_permission("manage_workspace_settings")
def discovered_item_form(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    recent_images = list(
        MediaAsset.objects.filter(
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).order_by("-created_at")[:12]
    )
    return render(
        request,
        "ugc/discovered_item_form.html",
        {
            "workspace": workspace,
            "platform_choices": PLATFORM_CHOICES,
            "target_choices": TARGET_CHOICES,
            "recent_images": recent_images,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_discovered_item(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    platform = request.POST.get("source_platform", "instagram").strip().lower()
    creator_handle = request.POST.get("contributor_handle", "").strip().lstrip("@")[:255]
    source_url = request.POST.get("source_url", "").strip()[:2000]
    external_id = request.POST.get("source_external_id", "").strip()[:255]
    discovery_query = request.POST.get("discovery_query", "").strip()[:500]
    target_type = request.POST.get("target_type", "").strip()[:100]
    target_id = request.POST.get("target_id", "").strip()[:255]
    media_asset_raw = request.POST.get("media_asset_id", "").strip()
    uploaded_media = request.FILES.get("media_upload")

    errors = []
    if platform not in dict(PLATFORM_CHOICES):
        errors.append("Choose a valid source platform.")
    if not creator_handle:
        errors.append("Creator handle is required for discovered content.")
    if not source_url:
        errors.append("Original post URL is required for discovered content.")
    if not target_type or not target_id:
        errors.append("TN Game target type and target ID are required.")

    if external_id:
        duplicate = UGCSubmission.objects.for_workspace(workspace.id).filter(
            metadata__provenance__platform=platform,
            metadata__provenance__external_id=external_id,
        ).exists()
        if duplicate:
            errors.append("That external post ID has already been imported for this source platform.")

    media_asset = None
    if media_asset_raw and not uploaded_media:
        media_asset = MediaAsset.objects.filter(
            id=media_asset_raw,
            workspace=workspace,
            media_type=MediaAsset.MediaType.IMAGE,
        ).first()
        if media_asset is None:
            errors.append("That image does not belong to this workspace.")

    if not uploaded_media and media_asset is None:
        errors.append("Choose or upload an image so the discovered card can be tested end to end.")

    if errors:
        for error in errors:
            messages.error(request, error)
        return redirect("ugc:discovered_item_form", workspace_id=workspace.id)

    if uploaded_media:
        try:
            media_asset = create_asset(
                organization=workspace.organization,
                workspace=workspace,
                uploaded_file=uploaded_media,
                uploaded_by=request.user,
                alt_text=request.POST.get("title", "").strip()[:255],
                title=request.POST.get("title", "").strip()[:255],
                tags=["community-content", "ugc", "discovered", platform],
            )
        except ValidationError as exc:
            upload_errors = exc.messages if hasattr(exc, "messages") else [str(exc)]
            for error in upload_errors:
                messages.error(request, error)
            return redirect("ugc:discovered_item_form", workspace_id=workspace.id)

        if media_asset.media_type != MediaAsset.MediaType.IMAGE:
            messages.error(request, "Discovered-item simulator uploads must be images.")
            return redirect("ugc:discovered_item_form", workspace_id=workspace.id)
        process_media_asset(str(media_asset.id))

    provenance = build_provenance(
        platform=platform,
        source_url=source_url,
        external_id=external_id,
        creator_handle=creator_handle,
        discovery_source="test_import",
        discovery_query=discovery_query,
    )
    metadata = set_provenance({}, provenance)
    metadata = set_permission(metadata, status=NOT_CONTACTED)
    metadata["discovery_simulator"] = True

    submission = UGCSubmission.objects.create(
        workspace=workspace,
        kind=UGCSubmission.Kind.PHOTO,
        status=UGCSubmission.Status.PENDING,
        source=UGCSubmission.Source.IMPORT,
        contributor_handle=creator_handle,
        contributor_name=request.POST.get("contributor_name", "").strip()[:255],
        contributor_external_id=request.POST.get("creator_external_id", "").strip()[:255],
        attribution=UGCSubmission.Attribution.HANDLE,
        target_type=target_type,
        target_id=target_id,
        target_label=request.POST.get("target_label", "").strip()[:255],
        target_url=request.POST.get("target_url", "").strip()[:2000],
        media_asset=media_asset,
        title=request.POST.get("title", "").strip()[:255],
        body=request.POST.get("body", "").strip(),
        consent_confirmed=False,
        metadata=metadata,
    )

    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="ugc.discovered_test_import",
        target=submission,
        target_label=str(submission),
        metadata={
            "platform": platform,
            "external_id": external_id,
            "creator_handle": creator_handle,
            "discovery_query": discovery_query,
            "media_asset_id": str(media_asset.id) if media_asset else "",
        },
        request=request,
    )
    messages.success(request, "Discovered item created. It is ready for the permission workflow.")
    return redirect(f"/workspace/{workspace.id}/community-content/?tab=discovered")
