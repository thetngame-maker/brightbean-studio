"""Calendar-period automation for partner/tourism impact reports."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.utils import timezone

from .audit import record_audit_event
from .models import AuditEvent, TourismImpactReport, TourismImpactReportSchedule
from .tourism_impact import build_impact_snapshot
from .tourism_impact_delivery import notify_impact_report_partners


def _zone(workspace) -> ZoneInfo:
    try:
        return ZoneInfo(workspace.effective_timezone or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        return ZoneInfo("UTC")


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def completed_period(cadence: str, *, as_of: date) -> tuple[date, date]:
    """Return the most recent fully completed local calendar period."""
    if cadence == TourismImpactReportSchedule.Cadence.WEEKLY:
        current_start = as_of - timedelta(days=as_of.weekday())
        return current_start - timedelta(days=7), current_start - timedelta(days=1)
    if cadence == TourismImpactReportSchedule.Cadence.QUARTERLY:
        current_start = _quarter_start(as_of)
        previous_end = current_start - timedelta(days=1)
        return _quarter_start(previous_end), previous_end
    current_start = date(as_of.year, as_of.month, 1)
    previous_end = current_start - timedelta(days=1)
    return date(previous_end.year, previous_end.month, 1), previous_end


def next_schedule_run(cadence: str, workspace, *, after=None) -> datetime:
    """Return the next 8am boundary in the workspace timezone."""
    after = after or timezone.now()
    zone = _zone(workspace)
    local_now = after.astimezone(zone)
    today = local_now.date()
    if cadence == TourismImpactReportSchedule.Cadence.WEEKLY:
        boundary = today - timedelta(days=today.weekday())
        candidate = datetime.combine(boundary, time(hour=8), tzinfo=zone)
        if candidate <= local_now:
            candidate += timedelta(days=7)
    elif cadence == TourismImpactReportSchedule.Cadence.QUARTERLY:
        boundary = _quarter_start(today)
        candidate = datetime.combine(boundary, time(hour=8), tzinfo=zone)
        if candidate <= local_now:
            month = boundary.month + 3
            year = boundary.year + int(month > 12)
            month = month - 12 if month > 12 else month
            candidate = datetime(year, month, 1, 8, tzinfo=zone)
    else:
        boundary = date(today.year, today.month, 1)
        candidate = datetime.combine(boundary, time(hour=8), tzinfo=zone)
        if candidate <= local_now:
            year = today.year + int(today.month == 12)
            month = 1 if today.month == 12 else today.month + 1
            candidate = datetime(year, month, 1, 8, tzinfo=zone)
    return candidate.astimezone(ZoneInfo("UTC"))


def _target(schedule):
    if not schedule.target_type or not schedule.target_id:
        return None
    return {
        "target_type": schedule.target_type,
        "target_id": schedule.target_id,
        "target_label": schedule.target_label,
        "target_url": schedule.target_url,
    }


def _title(schedule, period_start, period_end):
    start = period_start.strftime("%b %d").replace(" 0", " ")
    end = period_end.strftime("%b %d, %Y").replace(" 0", " ")
    return f"{schedule.name} · {start}–{end}"[:255]


def run_impact_report_schedule(schedule_id, *, actor=None, force=False, now=None):
    """Create at most one immutable report for a schedule/calendar period."""
    now = now or timezone.now()
    try:
        with transaction.atomic():
            schedule = (
                TourismImpactReportSchedule.objects.select_for_update()
                .select_related("workspace")
                .get(id=schedule_id)
            )
            if schedule.archived_at or (not force and (not schedule.is_active or schedule.next_run_at > now)):
                return None, False

            local_date = now.astimezone(_zone(schedule.workspace)).date()
            period_start, period_end = completed_period(schedule.cadence, as_of=local_date)
            existing = TourismImpactReport.objects.filter(
                source_schedule=schedule,
                period_start=period_start,
                period_end=period_end,
            ).first()
            if existing:
                schedule.last_run_at = now
                schedule.last_period_end = period_end
                schedule.last_error = ""
                schedule.next_run_at = next_schedule_run(schedule.cadence, schedule.workspace, after=now)
                schedule.save(
                    update_fields=["last_run_at", "last_period_end", "last_error", "next_run_at", "updated_at"]
                )
                return existing, False

            snapshot = build_impact_snapshot(
                schedule.workspace,
                period_start=period_start,
                period_end=period_end,
                target=_target(schedule),
                equivalent_cpm=schedule.equivalent_cpm,
            )
            status = (
                TourismImpactReport.Status.SHARED if schedule.auto_share else TourismImpactReport.Status.DRAFT
            )
            report = TourismImpactReport.objects.create(
                workspace=schedule.workspace,
                source_schedule=schedule,
                title=_title(schedule, period_start, period_end),
                period_start=period_start,
                period_end=period_end,
                target_type=schedule.target_type,
                target_id=schedule.target_id,
                target_label=schedule.target_label,
                target_url=schedule.target_url,
                status=status,
                snapshot=snapshot,
                equivalent_cpm=schedule.equivalent_cpm,
                generated_by=actor if getattr(actor, "is_authenticated", False) else None,
                shared_by=actor if status == TourismImpactReport.Status.SHARED else None,
                shared_at=now if status == TourismImpactReport.Status.SHARED else None,
            )
            schedule.last_run_at = now
            schedule.last_period_end = period_end
            schedule.last_error = ""
            schedule.next_run_at = next_schedule_run(schedule.cadence, schedule.workspace, after=now)
            schedule.save(
                update_fields=["last_run_at", "last_period_end", "last_error", "next_run_at", "updated_at"]
            )
            audit_metadata = {
                "schedule_id": str(schedule.id),
                "cadence": schedule.cadence,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "auto_share": schedule.auto_share,
            }
            record_audit_event(
                workspace=schedule.workspace,
                actor=actor,
                action="tourism_impact.scheduled_report_generated",
                target=report,
                metadata=audit_metadata,
                source=AuditEvent.Source.UI if actor else AuditEvent.Source.SYSTEM,
            )
            if schedule.auto_share:
                record_audit_event(
                    workspace=schedule.workspace,
                    actor=actor,
                    action="tourism_impact.scheduled_report_shared",
                    target=report,
                    metadata=audit_metadata,
                    source=AuditEvent.Source.UI if actor else AuditEvent.Source.SYSTEM,
                )
        if schedule.auto_share:
            notify_impact_report_partners(report)
        return report, True
    except Exception as exc:
        TourismImpactReportSchedule.objects.filter(id=schedule_id).update(last_error=str(exc)[:500])
        raise
