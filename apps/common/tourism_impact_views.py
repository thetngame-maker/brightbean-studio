"""Mobile-first Partner/Tourism Impact Report workflows."""

import csv
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from apps.members.decorators import require_permission
from apps.members.models import WorkspaceMembership

from .audit import record_audit_event
from .models import TourismImpactReport, TourismImpactReportSchedule
from .tourism_impact import build_impact_snapshot
from .tourism_impact_delivery import notify_impact_report_partners
from .tourism_impact_schedules import next_schedule_run, run_impact_report_schedule
from .ugc_creator_views import _safe_local_path
from .ugc_target_catalog import find_catalog_target, target_choices
from .ugc_views import _get_workspace

REPORT_PAGE_SIZE = 12


def _date(value, fallback):
    return parse_date(str(value or "").strip()) or fallback


def _target(workspace, value):
    value = str(value or "").strip()
    if not value:
        return None
    if "::" not in value:
        return False
    target_type, target_id = value.split("::", 1)
    return find_catalog_target(workspace, target_type, target_id) or False


def _cpm(value, fallback=Decimal("12")):
    try:
        result = Decimal(str(value or fallback)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if Decimal("0") <= result <= Decimal("1000") else None


def _optional_count(value):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return False
    return result if 0 <= result <= 1_000_000_000 else False


def _return_to(request, workspace, report=None):
    if report:
        fallback = reverse("ugc:impact_report_detail", kwargs={"workspace_id": workspace.id, "report_id": report.id})
    else:
        fallback = reverse("ugc:impact_reports", kwargs={"workspace_id": workspace.id})
    return _safe_local_path(request, request.POST.get("return_to"), fallback)


def _client_count(workspace):
    return WorkspaceMembership.objects.filter(
        workspace=workspace,
        workspace_role=WorkspaceMembership.WorkspaceRole.CLIENT,
    ).count()


def _conversion_rate(report):
    if report.website_visits and report.registrations is not None:
        return round((report.registrations / report.website_visits) * 100, 1)
    return None


@login_required
@require_permission("manage_workspace_settings")
def impact_reports(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    status = str(request.GET.get("view") or "active").strip().lower()
    if status not in {"active", "draft", "shared", "archived"}:
        status = "active"
    reports = TourismImpactReport.objects.for_workspace(workspace.id).select_related(
        "generated_by", "shared_by", "source_schedule"
    )
    if status == "active":
        reports = reports.exclude(status=TourismImpactReport.Status.ARCHIVED)
    else:
        reports = reports.filter(status=status)
    page = Paginator(reports, REPORT_PAGE_SIZE).get_page(request.GET.get("page") or 1)
    all_reports = TourismImpactReport.objects.for_workspace(workspace.id)
    counts = {
        "active": all_reports.exclude(status=TourismImpactReport.Status.ARCHIVED).count(),
        "draft": all_reports.filter(status=TourismImpactReport.Status.DRAFT).count(),
        "shared": all_reports.filter(status=TourismImpactReport.Status.SHARED).count(),
        "archived": all_reports.filter(status=TourismImpactReport.Status.ARCHIVED).count(),
    }
    today = timezone.localdate()
    schedule_queryset = TourismImpactReportSchedule.objects.for_workspace(workspace.id).filter(
        archived_at__isnull=True
    )
    active_schedule_count = schedule_queryset.filter(is_active=True).count()
    schedules = list(schedule_queryset.select_related("created_by")[:12])
    return render(
        request,
        "ugc/impact_reports.html",
        {
            "workspace": workspace,
            "impact_reports": page.object_list,
            "impact_page": page,
            "impact_counts": counts,
            "impact_view": status,
            "impact_target_choices": target_choices(workspace, limit=150),
            "impact_default_start": today - timedelta(days=29),
            "impact_default_end": today,
            "impact_client_count": _client_count(workspace),
            "impact_schedules": schedules,
            "impact_active_schedule_count": active_schedule_count,
            "impact_cadence_choices": TourismImpactReportSchedule.Cadence.choices,
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_impact_report(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:impact_reports", kwargs={"workspace_id": workspace.id})
    today = timezone.localdate()
    period_start = _date(request.POST.get("period_start"), today - timedelta(days=29))
    period_end = _date(request.POST.get("period_end"), today)
    if period_start > period_end or (period_end - period_start).days > 1095:
        messages.error(request, "Choose a report period of up to three years with the start before the end.")
        return redirect(fallback)
    target = _target(workspace, request.POST.get("target_key"))
    if target is False:
        messages.error(request, "Choose a destination from the existing TN Game target catalog.")
        return redirect(fallback)
    equivalent_cpm = _cpm(request.POST.get("equivalent_cpm"))
    if equivalent_cpm is None:
        messages.error(request, "Enter an equivalent CPM between $0 and $1,000.")
        return redirect(fallback)
    default_title = (
        f"{target['target_label'] + ' · ' if target else ''}{period_start:%b %-d}–{period_end:%b %-d, %Y} Impact"
    )
    title = (str(request.POST.get("title") or "").strip() or default_title)[:255]
    snapshot = build_impact_snapshot(
        workspace,
        period_start=period_start,
        period_end=period_end,
        target=target,
        equivalent_cpm=equivalent_cpm,
    )
    report = TourismImpactReport.objects.create(
        workspace=workspace,
        title=title,
        period_start=period_start,
        period_end=period_end,
        target_type=target["target_type"] if target else "",
        target_id=target["target_id"] if target else "",
        target_label=target["target_label"] if target else "",
        target_url=target.get("target_url") or "" if target else "",
        snapshot=snapshot,
        equivalent_cpm=equivalent_cpm,
        generated_by=request.user,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_impact.report_generated",
        target=report,
        metadata={
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "target_type": report.target_type,
            "target_id": report.target_id,
            "published_posts": snapshot["totals"]["published_posts"],
        },
        request=request,
    )
    messages.success(request, "Impact report generated from the current stored Studio data.")
    return redirect("ugc:impact_report_detail", workspace_id=workspace.id, report_id=report.id)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def create_impact_report_schedule(request, workspace_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:impact_reports", kwargs={"workspace_id": workspace.id})
    cadence = str(request.POST.get("cadence") or "").strip().lower()
    if cadence not in TourismImpactReportSchedule.Cadence.values:
        messages.error(request, "Choose a weekly, monthly, or quarterly report cadence.")
        return redirect(fallback)
    target = _target(workspace, request.POST.get("target_key"))
    if target is False:
        messages.error(request, "Choose a destination from the existing TN Game target catalog.")
        return redirect(fallback)
    equivalent_cpm = _cpm(request.POST.get("equivalent_cpm"))
    if equivalent_cpm is None:
        messages.error(request, "Enter an equivalent CPM between $0 and $1,000.")
        return redirect(fallback)
    cadence_label = dict(TourismImpactReportSchedule.Cadence.choices)[cadence]
    default_name = f"{target['target_label'] + ' · ' if target else ''}{cadence_label} Tourism Impact"
    schedule = TourismImpactReportSchedule.objects.create(
        workspace=workspace,
        name=(str(request.POST.get("name") or "").strip() or default_name)[:255],
        cadence=cadence,
        target_type=target["target_type"] if target else "",
        target_id=target["target_id"] if target else "",
        target_label=target["target_label"] if target else "",
        target_url=target.get("target_url") or "" if target else "",
        equivalent_cpm=equivalent_cpm,
        auto_share=str(request.POST.get("delivery_mode") or "share") == "share",
        next_run_at=next_schedule_run(cadence, workspace),
        created_by=request.user,
        updated_by=request.user,
    )
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_impact.schedule_created",
        target=schedule,
        metadata={
            "cadence": schedule.cadence,
            "auto_share": schedule.auto_share,
            "target_type": schedule.target_type,
            "target_id": schedule.target_id,
            "next_run_at": schedule.next_run_at.isoformat(),
        },
        request=request,
    )
    messages.success(request, "Recurring impact report scheduled from completed calendar periods.")
    return redirect(fallback)


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_impact_report_schedule(request, workspace_id, schedule_id):
    workspace = _get_workspace(request, workspace_id)
    fallback = reverse("ugc:impact_reports", kwargs={"workspace_id": workspace.id})
    schedule = get_object_or_404(
        TourismImpactReportSchedule.objects.for_workspace(workspace.id),
        id=schedule_id,
    )
    action = str(request.POST.get("action") or "").strip().lower()
    before = {"is_active": schedule.is_active, "archived": bool(schedule.archived_at)}
    if action == "pause":
        schedule.is_active = False
        schedule.updated_by = request.user
        schedule.save(update_fields=["is_active", "updated_by", "updated_at"])
        action_name = "tourism_impact.schedule_paused"
        message = "Recurring report paused. Existing snapshots remain unchanged."
    elif action == "resume":
        schedule.is_active = True
        schedule.archived_at = None
        schedule.next_run_at = next_schedule_run(schedule.cadence, workspace)
        schedule.updated_by = request.user
        schedule.save(
            update_fields=["is_active", "archived_at", "next_run_at", "updated_by", "updated_at"]
        )
        action_name = "tourism_impact.schedule_resumed"
        message = "Recurring report resumed."
    elif action == "archive":
        schedule.is_active = False
        schedule.archived_at = timezone.now()
        schedule.updated_by = request.user
        schedule.save(update_fields=["is_active", "archived_at", "updated_by", "updated_at"])
        action_name = "tourism_impact.schedule_archived"
        message = "Schedule archived. Its generated reports and audit history were preserved."
    elif action == "run":
        try:
            report, created = run_impact_report_schedule(schedule.id, actor=request.user, force=True)
        except Exception:
            messages.error(request, "Studio could not generate this scheduled report. The failure is recorded.")
            return redirect(fallback)
        if report is None:
            messages.error(request, "Archived schedules cannot be run.")
            return redirect(fallback)
        if created:
            messages.success(
                request,
                "Report generated and securely shared." if schedule.auto_share else "Internal report draft generated.",
            )
        else:
            messages.info(request, "That completed calendar period already has a report. Opening it instead.")
        return redirect("ugc:impact_report_detail", workspace_id=workspace.id, report_id=report.id)
    else:
        messages.error(request, "That schedule action is no longer available.")
        return redirect(fallback)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=action_name,
        target=schedule,
        metadata={"before": before, "after": {"is_active": schedule.is_active, "archived": bool(schedule.archived_at)}},
        request=request,
    )
    messages.success(request, message)
    return redirect(fallback)


@login_required
@require_permission("manage_workspace_settings")
def impact_report_detail(request, workspace_id, report_id):
    workspace = _get_workspace(request, workspace_id)
    report = get_object_or_404(
        TourismImpactReport.objects.for_workspace(workspace.id).select_related("generated_by", "shared_by"),
        id=report_id,
    )
    return render(
        request,
        "ugc/impact_report_detail.html",
        {
            "workspace": workspace,
            "report": report,
            "impact": report.snapshot or {},
            "impact_client_count": _client_count(workspace),
            "impact_is_partner_view": False,
            "impact_base_template": "base.html",
            "impact_conversion_rate": _conversion_rate(report),
        },
    )


@login_required
@require_permission("manage_workspace_settings")
@require_POST
def update_impact_report(request, workspace_id, report_id):
    workspace = _get_workspace(request, workspace_id)
    report = get_object_or_404(TourismImpactReport.objects.for_workspace(workspace.id), id=report_id)
    return_to = _return_to(request, workspace, report)
    action = str(request.POST.get("action") or "save").strip().lower()
    before_status = report.status
    if action == "save":
        visits = _optional_count(request.POST.get("website_visits"))
        registrations = _optional_count(request.POST.get("registrations"))
        if visits is False or registrations is False:
            messages.error(request, "Website visits and registrations must be non-negative whole numbers.")
            return redirect(return_to)
        report.title = (str(request.POST.get("title") or "").strip() or report.title)[:255]
        report.partner_notes = str(request.POST.get("partner_notes") or "").strip()[:10000]
        report.website_visits = visits
        report.registrations = registrations
        report.save(update_fields=["title", "partner_notes", "website_visits", "registrations", "updated_at"])
        action_name = "tourism_impact.report_updated"
        message = "Partner outcomes and report notes saved."
    elif action == "regenerate":
        equivalent_cpm = _cpm(request.POST.get("equivalent_cpm"), report.equivalent_cpm)
        if equivalent_cpm is None:
            messages.error(request, "Enter an equivalent CPM between $0 and $1,000.")
            return redirect(return_to)
        target = (
            {
                "target_type": report.target_type,
                "target_id": report.target_id,
                "target_label": report.target_label,
                "target_url": report.target_url,
            }
            if report.target_type and report.target_id
            else None
        )
        report.snapshot = build_impact_snapshot(
            workspace,
            period_start=report.period_start,
            period_end=report.period_end,
            target=target,
            equivalent_cpm=equivalent_cpm,
        )
        report.equivalent_cpm = equivalent_cpm
        report.generated_by = request.user
        report.generated_at = timezone.now()
        report.save(update_fields=["snapshot", "equivalent_cpm", "generated_by", "generated_at", "updated_at"])
        action_name = "tourism_impact.report_regenerated"
        message = "Report snapshot regenerated from current stored data."
    elif action == "share":
        report.status = TourismImpactReport.Status.SHARED
        report.shared_by = request.user
        report.shared_at = timezone.now()
        report.save(update_fields=["status", "shared_by", "shared_at", "updated_at"])
        notify_impact_report_partners(report)
        action_name = "tourism_impact.report_shared"
        message = "Report shared in the secure partner portal."
    elif action == "unshare":
        report.status = TourismImpactReport.Status.DRAFT
        report.shared_by = None
        report.shared_at = None
        report.save(update_fields=["status", "shared_by", "shared_at", "updated_at"])
        action_name = "tourism_impact.report_unshared"
        message = "Report removed from the partner portal."
    elif action == "archive":
        report.status = TourismImpactReport.Status.ARCHIVED
        report.save(update_fields=["status", "updated_at"])
        action_name = "tourism_impact.report_archived"
        message = "Report archived and removed from partner access."
    elif action == "restore":
        report.status = TourismImpactReport.Status.DRAFT
        report.shared_by = None
        report.shared_at = None
        report.save(update_fields=["status", "shared_by", "shared_at", "updated_at"])
        action_name = "tourism_impact.report_restored"
        message = "Report restored as an internal draft."
    else:
        messages.error(request, "That report action is no longer available.")
        return redirect(return_to)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action=action_name,
        target=report,
        metadata={"before_status": before_status, "after_status": report.status},
        request=request,
    )
    messages.success(request, message)
    return redirect(return_to)


def impact_csv_response(report):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="impact-report-{report.id}.csv"'
    writer = csv.writer(response)

    def write(row):
        # Partner-entered titles, notes, and target labels must not become
        # spreadsheet formulas when the export is opened in Excel or Sheets.
        safe = []
        for value in row:
            if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
                value = "'" + value
            safe.append(value)
        writer.writerow(safe)

    totals = (report.snapshot or {}).get("totals") or {}
    write(["TN Social Studio Partner/Tourism Impact Report"])
    write(["Title", report.title])
    write(["Period", report.period_start.isoformat(), report.period_end.isoformat()])
    write(["Target", report.target_label or "All known TN Game destinations"])
    write([])
    write(["Metric", "Value"])
    for key, label in (
        ("published_posts", "Published posts"),
        ("measured_exposure", "Measured exposure"),
        ("interactions", "Interactions"),
        ("shares_saves", "Shares and saves"),
        ("outbound_clicks", "Tracked outbound clicks"),
        ("tracked_link_clicks", "First-party campaign link clicks"),
        ("tracked_website_visits", "First-party unique daily website visits"),
        ("tracked_registrations", "First-party TN Game registrations"),
        ("tracked_conversion_rate", "First-party visit-to-registration rate (%)"),
        ("creator_participants", "Creator participants"),
        ("community_contributions", "Community contributions"),
        ("rights_cleared_assets", "Rights-cleared assets"),
        ("destinations_covered", "Destinations covered"),
        ("estimated_organic_value", "Equivalent media value"),
    ):
        write([label, totals.get(key, 0)])
    write(["Website visits (partner supplied)", report.website_visits if report.website_visits is not None else ""])
    write(
        ["TN Game registrations (partner supplied)", report.registrations if report.registrations is not None else ""]
    )
    campaigns = (report.snapshot or {}).get("campaign_attribution") or []
    if campaigns:
        write([])
        write(["First-party campaign attribution"])
        write(
            [
                "Campaign",
                "Target",
                "UTM campaign",
                "Link clicks",
                "Unique daily visits",
                "Registrations",
                "Conversion rate (%)",
            ]
        )
        for row in campaigns:
            write(
                [
                    row.get("name", ""),
                    row.get("target_label", ""),
                    row.get("utm_campaign", ""),
                    row.get("tracked_clicks", 0),
                    row.get("tracked_visits", 0),
                    row.get("registrations", 0),
                    row.get("conversion_rate", "") if row.get("conversion_rate") is not None else "",
                ]
            )
    write([])
    write(
        ["Destination", "Type", "Published posts", "Measured exposure", "Interactions", "Community posts", "Creators"]
    )
    for row in (report.snapshot or {}).get("target_breakdown") or []:
        write(
            [
                row.get("target_label", ""),
                row.get("target_type", ""),
                row.get("published_posts", 0),
                row.get("measured_exposure", 0),
                row.get("interactions", 0),
                row.get("community_posts", 0),
                row.get("creator_count", 0),
            ]
        )
    return response


@login_required
@require_permission("manage_workspace_settings")
def export_impact_report_csv(request, workspace_id, report_id):
    workspace = _get_workspace(request, workspace_id)
    report = get_object_or_404(TourismImpactReport.objects.for_workspace(workspace.id), id=report_id)
    record_audit_event(
        workspace=workspace,
        actor=request.user,
        action="tourism_impact.report_exported",
        target=report,
        metadata={"format": "csv"},
        request=request,
    )
    return impact_csv_response(report)
