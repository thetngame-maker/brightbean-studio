"""Shared helper for registering recurring django-background-tasks.

Centralizes the idempotent ``post_migrate`` registration pattern so each app
calls one function instead of copy-pasting the exists-check + error handling.
"""

import logging

from django.db import OperationalError, ProgrammingError
from django.utils import timezone

logger = logging.getLogger(__name__)


def register_recurring_task(task_func, *, repeat, verbose_name):
    """Ensure a healthy ``@background`` repeating task exists.

    Besides first-time registration, this repairs a task that is sitting in the
    library's exponential retry backoff. That matters for always-on pollers such
    as the social inbox: merely finding a row with the right verbose name is not
    enough if that row has already failed several times and its next attempt is
    hours or days away.

    Safe to call from a ``post_migrate`` handler. A fresh DB without the
    background-task tables raises a DB error, which we swallow quietly (the next
    ``migrate`` re-runs registration).
    """
    from background_task.models import Task

    try:
        task = (
            Task.objects.filter(verbose_name=verbose_name)
            .order_by("run_at")
            .first()
        )

        if task is None:
            task_func(repeat=repeat, verbose_name=verbose_name)
            logger.info("Registered recurring task %s (every %ds)", verbose_name, repeat)
            return

        update_fields = []

        # A failed execution is rescheduled by django-background-tasks with an
        # exponential delay and ``attempts`` > 0. On deploy, recover that chain
        # immediately so a transient provider/SLA failure cannot leave an
        # always-on sync dormant for hours or days.
        if task.attempts > 0 and task.locked_at is None:
            task.attempts = 0
            task.last_error = ""
            task.failed_at = None
            task.run_at = timezone.now()
            update_fields.extend(["attempts", "last_error", "failed_at", "run_at"])
            logger.warning(
                "Recovered recurring task %s from retry backoff",
                verbose_name,
            )

        # Keep deployed cadence changes in sync with already-registered rows.
        if task.repeat != repeat:
            task.repeat = repeat
            update_fields.append("repeat")

        if update_fields:
            task.save(update_fields=update_fields)
    except (OperationalError, ProgrammingError):
        # Fresh DB: the background-task tables don't exist yet. The next migrate
        # re-runs this registration, so skip quietly.
        logger.debug("Skipping %s registration (database not ready)", verbose_name)
    except Exception:
        logger.exception("Failed to register recurring task %s", verbose_name)
