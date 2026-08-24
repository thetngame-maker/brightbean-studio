"""Server-rendered Approved Smart Plan preview and commit flow."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.members.decorators import require_permission

from .ugc_smart_planning import (
    DEFAULT_PLAN_COUNT,
    PLAN_COUNTS,
    SmartPlanError,
    build_smart_plan,
    commit_smart_plan,
    plan_payload,
)
from .ugc_views import _get_workspace

PLAN_SIGNING_SALT = "ugc-approved-smart-plan-v1"


def _plan_count(value):
    try:
        parsed = int(value or DEFAULT_PLAN_COUNT)
    except (TypeError, ValueError):
        parsed = DEFAULT_PLAN_COUNT
    return parsed if parsed in PLAN_COUNTS else DEFAULT_PLAN_COUNT


@login_required
@require_permission("create_posts")
@require_http_methods(["GET", "POST"])
def approved_smart_plan(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    if request.method == "POST" and request.POST.get("action") == "commit":
        try:
            payload = signing.loads(
                request.POST.get("plan_token") or "",
                salt=PLAN_SIGNING_SALT,
                max_age=30 * 60,
            )
            posts, direct_schedule = commit_smart_plan(workspace, payload, actor=request.user)
        except SignatureExpired:
            messages.error(request, "This plan is more than 30 minutes old. Refresh it before scheduling.")
        except BadSignature:
            messages.error(request, "This Smart Plan could not be verified. Refresh it and try again.")
        except SmartPlanError as exc:
            messages.error(request, str(exc))
        else:
            if direct_schedule:
                messages.success(request, f"Smart Plan scheduled {len(posts)} community posts.")
            else:
                messages.success(
                    request,
                    f"Smart Plan created {len(posts)} timed drafts for the required approval workflow.",
                )
            return redirect("calendar:calendar", workspace_id=workspace.id)
        return redirect("ugc:approved_smart_plan", workspace_id=workspace.id)

    count = _plan_count(request.GET.get("count"))
    account_ids = request.GET.getlist("accounts")
    plan = build_smart_plan(workspace, count=count, account_ids=account_ids)
    token = ""
    if plan["items"]:
        token = signing.dumps(plan_payload(workspace, plan), salt=PLAN_SIGNING_SALT, compress=True)
    return_to = reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id}) + "?tab=approved&draft_state=ready"
    return render(
        request,
        "ugc/approved_smart_plan.html",
        {
            "workspace": workspace,
            "plan": plan,
            "plan_token": token,
            "plan_counts": PLAN_COUNTS,
            "selected_count": count,
            "selected_account_ids": {str(value) for value in account_ids},
            "return_to": return_to,
        },
    )
