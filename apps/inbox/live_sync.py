"""Request-side safety net for keeping Instagram inbox data fresh.

The background worker remains the primary ingestion path. When the Social Inbox
is actively open, however, its existing 12-second HTMX refreshes give us a safe
opportunity to repair a missing/stalled recurring task. This module throttles
that safety-net work so only one fast + deep Instagram poll is attempted per
workspace every few minutes rather than on every UI refresh.
"""

from __future__ import annotations

import logging

from django.core.cache import cache

from apps.social_accounts.models import SocialAccount

from .instagram_deep_sync import sync_instagram_account_deep
from .models import InboxMessage
from .tasks import InboxSyncEngine

logger = logging.getLogger(__name__)

# The inbox UI refreshes every 12 seconds. Four minutes keeps the visible inbox
# comfortably inside the five-minute background cadence while avoiding dozens
# of duplicate Graph API scans from those UI refreshes.
LIVE_INSTAGRAM_SYNC_THROTTLE_SECONDS = 4 * 60


def maybe_sync_instagram_workspace(workspace) -> int:
    """Run a throttled fast + deep Instagram sync for an actively viewed inbox.

    Returns the number of newly-created inbox rows. Failures are logged and kept
    non-fatal: a social API problem must never prevent the Inbox page itself from
    rendering. ``cache.add`` acts as the throttle/lock; with a shared cache it is
    cross-process, while the default local cache still limits each web worker.
    """
    cache_key = f"inbox:live-instagram-sync:{workspace.id}"
    if not cache.add(cache_key, "1", timeout=LIVE_INSTAGRAM_SYNC_THROTTLE_SECONDS):
        return 0

    accounts = list(
        SocialAccount.objects.filter(
            workspace=workspace,
            platform__in=("instagram_login", "instagram"),
            connection_status__in=(
                SocialAccount.ConnectionStatus.CONNECTED,
                SocialAccount.ConnectionStatus.TOKEN_EXPIRING,
            ),
        )
    )
    if not accounts:
        return 0

    before = InboxMessage.objects.filter(social_account__in=accounts).count()
    engine = InboxSyncEngine()

    for account in accounts:
        try:
            engine._sync_account(account)
        except Exception:
            logger.exception("Live Instagram fast sync failed for account %s", account.id)

        try:
            sync_instagram_account_deep(account)
        except Exception:
            logger.exception("Live Instagram deep sync failed for account %s", account.id)

    after = InboxMessage.objects.filter(social_account__in=accounts).count()
    added = max(0, after - before)
    logger.info(
        "Live Instagram inbox safety sync completed for workspace %s (%d new item(s))",
        workspace.id,
        added,
    )
    return added
