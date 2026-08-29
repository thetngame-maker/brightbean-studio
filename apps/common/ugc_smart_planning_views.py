"""Server-rendered Approved Smart Plan preview and commit flow."""

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from apps.members.decorators import require_permission

from .ugc_smart_captions import build_caption_drafts
from .ugc_smart_planning import (
    DEFAULT_PLAN_COUNT,
    DEFAULT_PLAN_DAYS,
    MAX_PLAN_COUNT,
    MAX_PLAN_DAYS,
    MIN_PLAN_COUNT,
    MIN_PLAN_DAYS,
    SmartPlanError,
    _connected_accounts,
    build_smart_plan,
    commit_smart_plan,
    plan_payload,
)
from .ugc_views import _get_workspace

logger = logging.getLogger(__name__)

PLAN_SIGNING_SALT = "ugc-approved-smart-plan-v1"


def _bounded_int(value, *, default, minimum, maximum):
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _retry_plan_items(workspace, payload, *, actor, caption_overrides):
    """Isolate an unexpected batch failure so one bad item cannot 500 the whole plan.

    ``commit_smart_plan`` is atomic for the complete payload, so an exception in
    the first attempt rolls the batch back. Retrying one row at a time lets valid
    rows commit while leaving the failing approved items untouched for review.
    No caption text or creator-rights data is written to logs.
    """
    created = []
    failed = 0
    direct_schedule = None
    for row in payload.get("items") or []:
        single_payload = {
            "workspace_id": payload.get("workspace_id"),
            "items": [row],
        }
        submission_id = str(row.get("submission_id") or "")
        account_id = str(row.get("account_id") or "")
        try:
            posts, item_direct_schedule = commit_smart_plan(
                workspace,
                single_payload,
                actor=actor,
                caption_overrides=caption_overrides,
            )
        except SmartPlanError as exc:
            failed += 1
            logger.warning(
                "Smart Plan item skipped after batch failure: %s",
                exc,
                extra={
                    "workspace_id": str(workspace.id),
                    "submission_id": submission_id,
                    "social_account_id": account_id,
                },
            )
        except Exception:
            failed += 1
            logger.exception(
                "Smart Plan item failed during isolated retry",
                extra={
                    "workspace_id": str(workspace.id),
                    "submission_id": submission_id,
                    "social_account_id": account_id,
                },
            )
        else:
            created.extend(posts)
            direct_schedule = item_direct_schedule
    return created, bool(direct_schedule), failed


@login_required
@require_permission("create_posts")
@require_http_methods(["GET", "POST"])
def approved_smart_plan(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    if request.method == "POST" and request.POST.get("action") == "commit":
        payload = None
        caption_overrides = {}
        try:
            payload = signing.loads(
                request.POST.get("plan_token") or "",
                salt=PLAN_SIGNING_SALT,
                max_age=30 * 60,
            )
            caption_overrides = {
                str(row.get("submission_id")): request.POST.get(f"caption_{row.get('submission_id')}", "")
                for row in payload.get("items") or []
            }
            posts, direct_schedule = commit_smart_plan(
                workspace, payload, actor=request.user, caption_overrides=caption_overrides
            )
        except SignatureExpired:
            messages.error(request, "This plan is more than 30 minutes old. Refresh it before scheduling.")
        except BadSignature:
            messages.error(request, "This Smart Plan could not be verified. Refresh it and try again.")
        except SmartPlanError as exc:
            messages.error(request, str(exc))
        except Exception:
            logger.exception(
                "Approved Smart Plan batch commit failed",
                extra={
                    "workspace_id": str(workspace.id),
                    "planned_item_count": len((payload or {}).get("items") or []),
                },
            )
            if payload and payload.get("items"):
                posts, direct_schedule, failed_count = _retry_plan_items(
                    workspace,
                    payload,
                    actor=request.user,
                    caption_overrides=caption_overrides,
                )
                if posts:
                    action = "scheduled" if direct_schedule else "created as timed drafts"
                    messages.warning(
                        request,
                        f"Smart Plan {action} {len(posts)} posts and safely skipped {failed_count} "
                        "item(s) that need review. Nothing was duplicated.",
                    )
                    return redirect("calendar:calendar", workspace_id=workspace.id)
            messages.error(
                request,
                "Smart Plan hit a server-side scheduling problem. No partial batch was left behind. "
                "Refresh the recommendations and try again; the failure has been logged for diagnosis.",
            )
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

    count = _bounded_int(
        request.GET.get("count"), default=DEFAULT_PLAN_COUNT, minimum=MIN_PLAN_COUNT, maximum=MAX_PLAN_COUNT
    )
    days = _bounded_int(
        request.GET.get("days"), default=DEFAULT_PLAN_DAYS, minimum=MIN_PLAN_DAYS, maximum=MAX_PLAN_DAYS
    )
    available_accounts = _connected_accounts(workspace)
    if request.GET.get("account_filter") == "1":
        account_ids = request.GET.getlist("accounts")
    else:
        account_ids = [str(account.id) for account in available_accounts]
    plan = build_smart_plan(workspace, count=count, days=days, account_ids=account_ids)
    smart_captions = request.GET.get("smart_captions") in {"1", "true", "on"}
    caption_status = build_caption_drafts(workspace, plan["items"], use_ai=smart_captions)
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
            "selected_count": count,
            "selected_days": days,
            "max_plan_count": MAX_PLAN_COUNT,
            "max_plan_days": MAX_PLAN_DAYS,
            "available_accounts": [
                {"account": account, "selected": str(account.id) in set(account_ids)}
                for account in available_accounts
            ],
            "selected_account_ids": {str(value) for value in account_ids},
            "smart_captions": smart_captions,
            "caption_status": caption_status,
            "return_to": return_to,
        },
    )
