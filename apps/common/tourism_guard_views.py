"""Mobile-first Tourism Safety and Accuracy Guard views."""

import re

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.core.validators import URLValidator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.composer.models import PlatformPost, Post
from apps.members.decorators import require_permission

from .audit import record_audit_event
from .models import TourismGuardReview, TourismGuardRule
from .tourism_guard import build_tourism_guard, findings_for_post
from .ugc_creator_views import _safe_local_path
from .ugc_target_catalog import find_catalog_target, target_choices
from .ugc_views import _get_workspace

GUARD_PAGE_SIZE = 12
VALID_VIEWS = {"check", "verified", "rules"}


def _phrase_list(value, *, limit=30):
    values = []
    seen = set()
    for item in re.split(r"[\r\n,]+", str(value or "")):
        phrase = item.strip().lower()[:120]
        if phrase and phrase not in seen:
            values.append(phrase)
            seen.add(phrase)
    return values[:limit]


def _choice(value, choices, fallback):
    value = str(value or "").strip().lower()
    return value if value in {key for key, _label in choices} else fallback


def _return_to(request, workspace):
    fallback = reverse("ugc:tourism_guard", kwargs={"workspace_id": workspace.id})
    return _safe_local_path(request, request.POST.get("return_to"), fallback)


@login_required
@require_permission("manage_workspace_settings")
def tourism_guard(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    guard = build_tourism_guard(workspace)
    active_view = str(request.GET.get("view") or "check").strip().lower()
    if active_view not in VALID_VIEWS:
        active_view = "check"
    query = str(request.GET.get("q") or "").strip()[:120]
    rows = guard["verified_rows"] if active_view == "verified" else guard["rows"]
    if query and active_view != "rules":
        lowered = query.lower()
        rows = [
            row
            for row in rows
            if lowered in (row["post"].title or "").lower()
            or lowered in (row["post"].caption or "").lower()
            or any(lowered in finding["rule"]["title"].lower() for finding in row["findings"])
            or any(lowered in (finding["target"].get("target_label") or "").lower() for finding in row["findings"])
        ]
    page = Paginator(rows, GUARD_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    return render(
        request,
        "ugc/tourism_guard.html",
        {
            "workspace": workspace,
            "guard": guard,
            "guard_rows": page.object_list,
            "guard_page": page,
            "guard_view": active_view,
            "guard_query": query,
            "guard_target_choices": target_choices(workspace, limit=150),
            "guard_kind_choices": TourismGuardRule.Kind.choices,
            "guard_severity_choices": TourismGuardRule.Severity.choices,
        },
    )


def _rule_values(request, workspace):
    target_key = str(request.POST.get("target_key") or "").strip()
    target = None
    if "::" in target_key:
        target_type, target_id = target_key.split("::", 1)
        target = find_catalog_target(workspace, target_type, target_id)
    title = str(request.POST.get("title") or "").strip()[:255]
    guidance = str(request.POST.get("guidance") or "").strip()[:5000]
    source_url = str(request.POST.get("source_url") or "").strip()[:2000]
    try:
        URLValidator()(source_url)
    except ValidationError:
        source_url = ""
    if target is None or not title or not guidance or not source_url:
        return None
    return {
        "target_type": target["target_type"],
        "target_id": target["target_id"],
        "target_label": target["target_label"],
        "target_url": target.get("target_url") or "",
        "kind": _choice(request.POST.get("kind"), TourismGuardRule.Kind.choices, TourismGuardRule.Kind.SAFETY),
        "severity": _choice(
            request.POST.get("severity"),
            TourismGuardRule.Severity.choices,
            TourismGuardRule.Severity.WARNING,
        ),
        "title": title,
        "guidance": guidance,
        "trigger_phrases": _phrase_list(request.POST.get("trigger_phrases")),
        "safe_context_phrases": _phrase_list(request.POST.get("safe_context_phrases")),
        "source_url": source_url,
        "source_label": str(request.POST.get("source_label") or "").strip()[:255],
        "verified_at": timezone.now(),
    }


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_tourism_guard_rule(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    return_to = _return_to(request, workspace)
    values = _rule_values(request, workspace)
    if values is None:
        messages.error(request, "Choose an existing TN Game target and add guidance plus a valid official source URL.")
        return redirect(return_to)
    rule = TourismGuardRule.objects.create(
        workspace=workspace,
        created_by=request.user,
        updated_by=request.user,
        **values,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_guard.rule_created",
        target=rule,
        metadata={
            "target_type": rule.target_type,
            "target_id": rule.target_id,
            "severity": rule.severity,
            "source_url": rule.source_url,
        },
        request=request,
    )
    messages.success(request, "Tourism guard rule added. Matching drafts will be checked immediately.")
    return redirect(return_to)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_tourism_guard_rule(request, workspace_id, rule_id):
    workspace = _get_workspace(request, workspace_id)
    return_to = _return_to(request, workspace)
    rule = get_object_or_404(TourismGuardRule.objects.for_workspace(workspace.id), id=rule_id)
    action = str(request.POST.get("action") or "save").strip().lower()
    before = {
        "is_active": rule.is_active,
        "severity": rule.severity,
        "title": rule.title,
        "source_url": rule.source_url,
    }
    if action == "toggle":
        rule.is_active = not rule.is_active
        fields = ["is_active", "updated_by", "updated_at"]
    else:
        values = _rule_values(request, workspace)
        if values is None:
            messages.error(
                request, "Choose an existing TN Game target and add guidance plus a valid official source URL."
            )
            return redirect(return_to)
        for field, value in values.items():
            setattr(rule, field, value)
        fields = [*values, "updated_by", "updated_at"]
    rule.updated_by = request.user
    rule.save(update_fields=fields)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_guard.rule_updated",
        target=rule,
        metadata={
            "before": before,
            "after": {
                "is_active": rule.is_active,
                "severity": rule.severity,
                "title": rule.title,
                "source_url": rule.source_url,
            },
        },
        request=request,
    )
    messages.success(request, "Tourism guard rule updated. Previous verifications will be rechecked when needed.")
    return redirect(return_to)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def verify_tourism_guard_finding(request, workspace_id, post_id):
    workspace = _get_workspace(request, workspace_id)
    return_to = _return_to(request, workspace)
    get_object_or_404(Post, id=post_id, workspace=workspace)
    rule_key = str(request.POST.get("rule_key") or "").strip()[:100]
    action = str(request.POST.get("action") or "verify").strip().lower()
    post, findings, verified = findings_for_post(workspace, post_id)
    finding = next((item for item in [*findings, *verified] if item["rule_key"] == rule_key), None)
    if finding is None:
        messages.info(request, "That finding is no longer present because the content or rule changed.")
        return redirect(return_to)
    review = TourismGuardReview.objects.for_workspace(workspace.id).filter(post=post, rule_key=rule_key).first()
    if action == "reopen":
        if review is not None:
            before = review.finding_fingerprint
            review.finding_fingerprint = ""
            review.reviewed_by = request.user
            review.reviewed_at = timezone.now()
            review.save(update_fields=["finding_fingerprint", "reviewed_by", "reviewed_at", "updated_at"])
            record_audit_event(
                workspace=workspace,
                actor=request.user,
                action="tourism_guard.review_reopened",
                target=post,
                metadata={"rule_key": rule_key, "previous_fingerprint": before},
                request=request,
            )
        messages.success(request, "Finding returned to Needs check.")
        return redirect(return_to)
    note = str(request.POST.get("note") or "").strip()[:5000]
    if finding["severity"] == TourismGuardRule.Severity.BLOCKER and len(note) < 10:
        messages.error(request, "Add a short verification reason before overriding a publication blocker.")
        return redirect(return_to)
    before = review.finding_fingerprint if review else ""
    review, _created = TourismGuardReview.objects.update_or_create(
        workspace=workspace,
        post=post,
        rule_key=rule_key,
        defaults={
            "finding_fingerprint": finding["fingerprint"],
            "note": note,
            "reviewed_by": request.user,
            "reviewed_at": timezone.now(),
        },
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_guard.finding_verified",
        target=post,
        metadata={
            "rule_key": rule_key,
            "severity": finding["severity"],
            "previous_fingerprint": before,
            "finding_fingerprint": review.finding_fingerprint,
            "reason": note,
        },
        request=request,
    )
    # Release only system-owned Tourism Guard holds, and only after every
    # blocker for this exact revision has a current verification. A client
    # hold or any unrelated publish error remains untouched.
    released_ids = []
    if finding["severity"] == TourismGuardRule.Severity.BLOCKER:
        from .tourism_guard import blocking_findings_for_post

        if not blocking_findings_for_post(workspace, post.id):
            guard_holds = post.platform_posts.filter(
                status=PlatformPost.Status.ON_HOLD,
                publish_error__startswith="Publication held by Tourism Guard:",
            )
            released_ids = [str(row_id) for row_id in guard_holds.values_list("id", flat=True)]
            guard_holds.update(status=PlatformPost.Status.SCHEDULED, publish_error="")
    if released_ids:
        record_audit_event(
            workspace=workspace,
            actor=request.user,
            action="tourism_guard.publish_released",
            target=post,
            metadata={"platform_post_ids": released_ids, "rule_key": rule_key},
            request=request,
        )
    messages.success(request, "Finding verified for this exact content revision.")
    return redirect(return_to)
