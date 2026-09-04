"""Mobile Share Sheet handoff for rights-aware Studio drafts."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.composer.captured_drafts import (
    canonical_source_url,
    create_captured_draft,
    find_existing_capture,
)
from apps.media_library.quotas import StorageQuotaExceededError
from apps.media_library.services import create_asset
from apps.members.models import WorkspaceMembership
from apps.social_accounts.models import SocialAccount


def _resolve_workspace(request):
    membership = getattr(request, "workspace_membership", None)
    if membership is None:
        membership = (
            WorkspaceMembership.objects.filter(user=request.user, workspace__is_archived=False)
            .select_related("workspace__organization", "custom_role")
            .order_by("workspace__name")
            .first()
        )
        if membership is not None:
            request.workspace_membership = membership
            request.workspace = membership.workspace
            if request.user.last_workspace_id != membership.workspace_id:
                request.user.last_workspace_id = membership.workspace_id
                request.user.save(update_fields=["last_workspace_id"])
    if membership is None:
        raise PermissionDenied("You are not a member of an active workspace.")
    if not membership.effective_permissions.get("create_posts", False):
        raise PermissionDenied("Permission denied: create_posts")
    return membership.workspace, membership


def _form_values(request):
    source = request.POST.get("source_url", "") if request.method == "POST" else request.GET.get("source", "")
    if not source and request.method == "GET":
        source = request.GET.get("url", "")
    return {
        "source_url": str(source or "").strip()[:2000],
        "social_account_id": str(request.POST.get("social_account_id", "")).strip(),
        "creator_handle": str(request.POST.get("creator_handle", "")).strip()[:255],
        "creator_name": str(request.POST.get("creator_name", "")).strip()[:255],
        "title": str(request.POST.get("title", "")).strip()[:255],
        "caption": str(request.POST.get("caption", ""))[:10_000],
    }


def _render_capture(request, *, workspace, membership, accounts, form, error="", status=200):
    can_upload_media = membership.effective_permissions.get("upload_media", False)
    if not form["social_account_id"] and accounts:
        saved_account_id = str(request.session.get("ios_capture_social_account_id", ""))
        allowed_ids = {str(account.id) for account in accounts}
        form["social_account_id"] = saved_account_id if saved_account_id in allowed_ids else str(accounts[0].id)
    return render(
        request,
        "composer/mobile_capture.html",
        {
            "workspace": workspace,
            "accounts": accounts,
            "form": form,
            "error": error,
            "can_upload_media": can_upload_media,
        },
        status=status,
    )


@login_required
@require_http_methods(["GET", "POST"])
def mobile_capture(request):
    """Review iOS Share Sheet input and turn it into a Studio draft."""
    workspace, membership = _resolve_workspace(request)
    accounts = list(
        SocialAccount.objects.for_workspace(workspace.id)
        .filter(connection_status=SocialAccount.ConnectionStatus.CONNECTED)
        .order_by("platform", "account_name")
    )
    form = _form_values(request)

    if request.method == "GET":
        return _render_capture(
            request,
            workspace=workspace,
            membership=membership,
            accounts=accounts,
            form=form,
        )

    account = next((item for item in accounts if str(item.id) == form["social_account_id"]), None)
    if account is None:
        return _render_capture(
            request,
            workspace=workspace,
            membership=membership,
            accounts=accounts,
            form=form,
            error="Choose a connected Studio account.",
            status=400,
        )

    try:
        source_url = canonical_source_url(form["source_url"])
    except ValueError as exc:
        return _render_capture(
            request,
            workspace=workspace,
            membership=membership,
            accounts=accounts,
            form=form,
            error=str(exc),
            status=400,
        )
    form["source_url"] = source_url

    existing = find_existing_capture(workspace=workspace, social_account=account, source_url=source_url)
    if existing is not None:
        messages.info(request, "That source is already in Studio. Opening its existing draft.")
        return redirect(
            "composer:compose_edit",
            workspace_id=workspace.id,
            post_id=existing.post.id,
        )

    uploaded_file = request.FILES.get("media")
    if uploaded_file and not membership.effective_permissions.get("upload_media", False):
        raise PermissionDenied("Permission denied: upload_media")

    media_assets = []
    if uploaded_file:
        try:
            media_assets.append(
                create_asset(
                    organization=workspace.organization,
                    workspace=workspace,
                    uploaded_file=uploaded_file,
                    uploaded_by=request.user,
                    title=form["title"] or uploaded_file.name,
                    tags=["ios-shortcut", "ugc-capture"],
                )
            )
        except ValidationError as exc:
            error = "; ".join(exc.messages) if getattr(exc, "messages", None) else str(exc)
            return _render_capture(
                request,
                workspace=workspace,
                membership=membership,
                accounts=accounts,
                form=form,
                error=error,
                status=400,
            )
        except StorageQuotaExceededError:
            return _render_capture(
                request,
                workspace=workspace,
                membership=membership,
                accounts=accounts,
                form=form,
                error="This workspace does not have enough media storage remaining for that file.",
                status=413,
            )

    try:
        result = create_captured_draft(
            workspace=workspace,
            social_account=account,
            source_url=source_url,
            creator_handle=form["creator_handle"],
            creator_name=form["creator_name"],
            title=form["title"],
            caption=form["caption"],
            media_assets=media_assets,
            actor=request.user,
            request=request,
            capture_channel="ios_shortcut",
            capture_label="the TN Social Studio iPhone Shortcut",
            audit_source="ui",
        )
    except ValueError as exc:
        return _render_capture(
            request,
            workspace=workspace,
            membership=membership,
            accounts=accounts,
            form=form,
            error=str(exc),
            status=400,
        )

    request.session["ios_capture_social_account_id"] = str(account.id)
    messages.success(
        request,
        "Draft created from your iPhone share. Review creator rights before scheduling.",
    )
    edit_url = reverse(
        "composer:compose_edit",
        kwargs={"workspace_id": workspace.id, "post_id": result.post.id},
    )
    return redirect(f"{edit_url}?capture=ios")
