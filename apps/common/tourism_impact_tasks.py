"""Background sweep for recurring partner impact reports."""

import logging

from background_task import background
from django.utils import timezone

from .models import TourismImpactReportSchedule
from .tourism_impact_schedules import run_impact_report_schedule

logger = logging.getLogger(__name__)

IMPACT_REPORT_SCAN_INTERVAL_SECONDS = 15 * 60


@background(schedule=0)
def run_due_impact_report_schedules():
    due_ids = list(
        TourismImpactReportSchedule.objects.filter(
            is_active=True,
            archived_at__isnull=True,
            next_run_at__lte=timezone.now(),
        )
        .order_by("next_run_at")
        .values_list("id", flat=True)[:100]
    )
    for schedule_id in due_ids:
        try:
            run_impact_report_schedule(schedule_id)
        except Exception:
            logger.exception("Recurring impact report schedule %s failed", schedule_id)
