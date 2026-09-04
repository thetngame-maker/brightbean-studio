"""Server-rendered Approved Smart Plan preview and commit flow."""

import logging
import re
import zoneinfo
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_http_methods

from apps.members.decorators import require_permission

from .ugc_smart_captions import build_caption_drafts
from .ugc_smart_planning import (
    DEFAULT_DAILY_LIMIT,
    DEFAULT_PLAN_COUNT,
    DEFAULT_PLAN_DAYS,
    MAX_DAILY_LIMIT,
    MAX_PLAN_COUNT,
    MAX_PLAN_DAYS,
    MIN_DAILY_LIMIT,
    MIN_PLAN_COUNT,
    MIN_PLAN_DAYS,
    SmartPlanError,
    _connected_accounts,
    build_smart_plan,
    plan_payload,
)
from .ugc_smart_planning_commit import commit_smart_plan
from .ugc_views import _get_workspace

logger = logging.getLogger(__name__)

PLAN_SIGNING_SALT = "ugc-approved-smart-plan-v1"
WEEKDAY_OPTIONS = ((0, "Mon"), (1, "Tue"), (2, "Wed"), (3, "Thu"), (4, "Fri"), (5, "Sat"), (6, "Sun"))


def _bounded_int(value, *, default, minimum, maximum):
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _planning_window(request, workspace, fallback_days):
    workspace_tz = zoneinfo.ZoneInfo(workspace.effective_timezone or "UTC")
    today = timezone.now().astimezone(workspace_tz).date()
    last_available = today + timedelta(days=MAX_PLAN_DAYS - 1)
    start = parse_date(request.GET.get("start_date") or "") or today
    start = max(today, min(last_available, start))
    default_end = start + timedelta(days=fallback_days - 1)
    end = parse_date(request.GET.get("end_date") or "") or default_end
    end = max(start, min(last_available, end))
    return today, last_available, start, end


def _selected_weekdays(request):
    if request.GET.get("weekday_filter") != "1":
        return set(range(7))
    selected = set()
    for value in request.GET.getlist("weekdays"):
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6:
            selected.add(day)
    return selected


def _date_limits(request, start, end):
    limits = {}
    day = start
    while day <= end:
        key = f"date_limit_{day.isoformat()}"
        if request.GET.get(key) not in {None, ""}:
            limits[day] = _bounded_int(
                request.GET.get(key),
                default=0,
                minimum=0,
                maximum=MAX_DAILY_LIMIT,
            )
        day += timedelta(days=1)
    return limits


def _safe_failure_reason(exc):
    """Return an actionable diagnostic without exposing request or rights data."""
    if isinstance(exc, (ValueError, AttributeError, TypeError)):
        detail = re.sub(r"https?://\S+", "[link]", str(exc or ""))
        detail = re.sub(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
            "[id]",
            detail,
        )
        detail = " ".join(detail.split())[:240]
        if detail:
            return f"{exc.__class__.__name__}: {detail}"
    return f"{exc.__class__.__name__} in the scheduling service"


def _retry_plan_items(workspace, payload, *, actor, caption_overrides):
    """Isolate an unexpected batch failure so one bad item cannot 500 the whole plan.

    ``commit_smart_plan`` is atomic for the complete payload, so an exception in
    the first attempt rolls the batch back. Retrying one row at a time lets valid
    rows commit while leaving the failing approved items untouched for review.
    No caption text or creator-rights data is written to logs.
    """
    created = []
    failed = 0
    failure_reasons = []
    direct_schedule = None
    for row in payload.get("items") or []:
        single_payload = {
            "workspace_id": payload.get("workspace_id"),
            "constraints": payload.get("constraints"),
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
            failure_reasons.append(str(exc))
            logger.warning(
                "Smart Plan item skipped after batch failure: %s",
                exc,
                extra={
                    "workspace_id": str(workspace.id),
                    "submission_id": submission_id,
                    "social_account_id": account_id,
                },
            )
        except Exception as exc:
            failed += 1
            failure_reasons.append(_safe_failure_reason(exc))
            logger.exception(
                "Smart Plan item failed during isolated retry",
                extra={
                    "workspace_id": str(workspace.id),
                    "submission_id": submission_id,
                    "social_account_id": account_id,
                    "failure_type": exc.__class__.__name__,
                },
            )
        else:
            created.extend(posts)
            direct_schedule = item_direct_schedule
    distinct_reasons = list(dict.fromkeys(reason for reason in failure_reasons if reason))
    failure_reason = distinct_reasons[0] if len(distinct_reasons) == 1 else ""
    return created, bool(direct_schedule), failed, failure_reason


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
        except Exception as exc:
            logger.exception(
                "Approved Smart Plan batch commit failed",
                extra={
                    "workspace_id": str(workspace.id),
                    "planned_item_count": len((payload or {}).get("items") or []),
                    "failure_type": exc.__class__.__name__,
                },
            )
            failure_reason = _safe_failure_reason(exc)
            if payload and payload.get("items"):
                posts, direct_schedule, failed_count, isolated_reason = _retry_plan_items(
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
                if isolated_reason:
                    failure_reason = isolated_reason
            messages.error(
                request,
                "Smart Plan could not schedule any posts. No partial batch was left behind. "
                f"Diagnostic: {failure_reason}. This has also been logged for repair.",
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
    daily_limit = _bounded_int(
        request.GET.get("daily_limit"),
        default=DEFAULT_DAILY_LIMIT,
        minimum=MIN_DAILY_LIMIT,
        maximum=MAX_DAILY_LIMIT,
    )
    today, max_plan_date, start_date, end_date = _planning_window(request, workspace, days)
    days = (end_date - start_date).days + 1
    selected_weekdays = _selected_weekdays(request)
    date_limits = _date_limits(request, start_date, end_date)
    available_accounts = _connected_accounts(workspace)
    if request.GET.get("account_filter") == "1":
        account_ids = request.GET.getlist("accounts")
    else:
        account_ids = [str(account.id) for account in available_accounts]
    plan = build_smart_plan(
        workspace,
        count=count,
        days=days,
        account_ids=account_ids,
        start_date=start_date,
        end_date=end_date,
        weekdays=selected_weekdays,
        daily_limit=daily_limit,
        date_limits=date_limits,
    )
    smart_captions = request.GET.get("smart_captions") in {"1", "true", "on"}
    caption_status = build_caption_drafts(workspace, plan["items"], use_ai=smart_captions)
    token = ""
    if plan["items"]:
        token = signing.dumps(plan_payload(workspace, plan), salt=PLAN_SIGNING_SALT, compress=True)
    return_to = (
        reverse("ugc:moderation_queue", kwargs={"workspace_id": workspace.id}) + "?tab=approved&draft_state=ready"
    )
    return render(
        request,
        "ugc/approved_smart_plan.html",
        {
            "workspace": workspace,
            "plan": plan,
            "plan_token": token,
            "selected_count": count,
            "selected_days": days,
            "selected_daily_limit": daily_limit,
            "selected_start_date": start_date,
            "selected_end_date": end_date,
            "selected_weekdays": selected_weekdays,
            "weekday_options": WEEKDAY_OPTIONS,
            "today": today,
            "max_plan_date": max_plan_date,
            "plan_dates": [
                {
                    "date": start_date + timedelta(days=offset),
                    "override": date_limits.get(start_date + timedelta(days=offset), ""),
                    "planned": plan["daily_counts"].get(start_date + timedelta(days=offset), 0),
                    "existing": plan["existing_daily_counts"].get(start_date + timedelta(days=offset), 0),
                    "enabled": (start_date + timedelta(days=offset)).weekday() in selected_weekdays,
                }
                for offset in range(days)
            ],
            "max_plan_count": MAX_PLAN_COUNT,
            "max_plan_days": MAX_PLAN_DAYS,
            "max_daily_limit": MAX_DAILY_LIMIT,
            "available_accounts": [
                {"account": account, "selected": str(account.id) in set(account_ids)} for account in available_accounts
            ],
            "selected_account_ids": {str(value) for value in account_ids},
            "smart_captions": smart_captions,
            "caption_status": caption_status,
            "return_to": return_to,
        },
    )
