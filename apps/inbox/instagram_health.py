"""Instagram production-readiness dashboard for TN Social Studio."""

import logging
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.composer.models import PlatformPost
from apps.members.decorators import require_permission
from apps.social_accounts.models import SocialAccount

from . import views
from .instagram_deep_sync import sync_instagram_account_deep
from .models import InboxMessage, InboxReply
from .tasks import InboxSyncEngine

logger = logging.getLogger(__name__)


@login_required
@require_permission("use_inbox")
def instagram_health(request, workspace_id):
    workspace = views._get_workspace(request, workspace_id)
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    accounts = list(
        SocialAccount.objects.filter(
            workspace=workspace,
            platform__in=("instagram_login", "instagram"),
        ).order_by("account_name", "connected_at")
    )

    rows = []
    ready_count = 0
    attention_count = 0

    for account in accounts:
        inbox_messages = InboxMessage.objects.filter(social_account=account)
        replies = InboxReply.objects.filter(inbox_message__social_account=account)
        posts = PlatformPost.objects.filter(social_account=account)

        latest_message = inbox_messages.order_by("-received_at").only(
            "received_at", "message_type", "sender_name"
        ).first()
        latest_reply = replies.exclude(platform_reply_id="").order_by("-sent_at").only(
            "sent_at", "platform_reply_id"
        ).first()

        inbound_24h = inbox_messages.filter(received_at__gte=day_ago).count()
        inbound_7d = inbox_messages.filter(received_at__gte=week_ago).count()
        dms_7d = inbox_messages.filter(
            received_at__gte=week_ago,
            message_type=InboxMessage.MessageType.DM,
        ).count()
        comments_7d = inbox_messages.filter(
            received_at__gte=week_ago,
            message_type=InboxMessage.MessageType.COMMENT,
        ).count()
        replies_7d = replies.filter(sent_at__gte=week_ago).exclude(platform_reply_id="").count()
        published_7d = posts.filter(
            status=PlatformPost.Status.PUBLISHED,
            published_at__gte=week_ago,
        ).count()
        failed_7d = posts.filter(
            status=PlatformPost.Status.FAILED,
            updated_at__gte=week_ago,
        ).count()

        token_days = None
        if account.token_expires_at:
            token_days = (account.token_expires_at - now).total_seconds() / 86400
            if token_days < 0:
                token_state = "expired"
            elif token_days <= 7:
                token_state = "expiring"
            else:
                token_state = "healthy"
        elif account.oauth_access_token:
            token_state = "present"
        else:
            token_state = "missing"

        issues = []
        warnings = []
        if account.connection_status in {
            SocialAccount.ConnectionStatus.DISCONNECTED,
            SocialAccount.ConnectionStatus.ERROR,
        }:
            issues.append("Instagram connection needs attention")
        elif account.connection_status == SocialAccount.ConnectionStatus.TOKEN_EXPIRING:
            warnings.append("Access token is marked as expiring")

        if token_state == "missing":
            issues.append("No Instagram access token is stored")
        elif token_state == "expired":
            issues.append("Instagram access token is expired")
        elif token_state == "expiring":
            warnings.append("Instagram token expires within 7 days")

        if account.webhooks_active is False:
            issues.append("Instagram comments/messages webhook is inactive")
        elif account.webhooks_active is None:
            warnings.append("Instagram webhook subscription has not been confirmed")

        if account.webhook_needs_reconnect:
            issues.append("Instagram webhook permissions require reconnecting")
        elif account.webhook_error:
            warnings.append(account.webhook_error[:180])
        if account.last_error:
            warnings.append(account.last_error[:180])
        if failed_7d:
            warnings.append(f"{failed_7d} Instagram publish failure{'s' if failed_7d != 1 else ''} in the last 7 days")

        core_ready = (
            account.connection_status
            in {
                SocialAccount.ConnectionStatus.CONNECTED,
                SocialAccount.ConnectionStatus.TOKEN_EXPIRING,
            }
            and token_state not in {"missing", "expired"}
            and account.webhooks_active is True
            and not account.webhook_needs_reconnect
        )

        if core_ready and not issues:
            ready_count += 1
            state = "ready" if not warnings else "warning"
        else:
            attention_count += 1
            state = "error" if issues else "warning"

        rows.append(
            {
                "account": account,
                "state": state,
                "core_ready": core_ready,
                "issues": issues,
                "warnings": warnings,
                "token_state": token_state,
                "token_days": token_days,
                "latest_message": latest_message,
                "latest_reply": latest_reply,
                "inbound_24h": inbound_24h,
                "inbound_7d": inbound_7d,
                "dms_7d": dms_7d,
                "comments_7d": comments_7d,
                "replies_7d": replies_7d,
                "published_7d": published_7d,
                "failed_7d": failed_7d,
            }
        )

    total_accounts = len(rows)
    all_ready = bool(total_accounts and ready_count == total_accounts)

    return render(
        request,
        "inbox/instagram_health.html",
        {
            "workspace": workspace,
            "accounts": rows,
            "total_accounts": total_accounts,
            "ready_count": ready_count,
            "attention_count": attention_count,
            "all_ready": all_ready,
            "checked_at": now,
        },
    )


@login_required
@require_permission("use_inbox")
@require_POST
def sync_instagram_now(request, workspace_id):
    """Immediately run both fast and deep Instagram inbox recovery polls.

    The fast pass mirrors the recurring inbox sync. The deep pass follows the
    Instagram media cursor across additional pages, so this button is also a
    useful recovery/diagnostic tool when Meta's webhook delivery is delayed.
    """
    workspace = views._get_workspace(request, workspace_id)
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
        messages.warning(request, "No connected Instagram accounts are available to sync.")
        return redirect("inbox:instagram_health", workspace_id=workspace.id)

    before = InboxMessage.objects.filter(social_account__in=accounts).count()
    engine = InboxSyncEngine()
    failures = []
    deep_added = 0

    for account in accounts:
        try:
            engine._sync_account(account)
        except Exception as exc:
            logger.exception("Manual Instagram fast sync failed for account %s", account.id)
            failures.append(f"{account.account_name}: fast sync")

        try:
            deep_added += sync_instagram_account_deep(account)
        except Exception as exc:
            logger.exception("Manual Instagram deep sync failed for account %s", account.id)
            failures.append(f"{account.account_name}: deep sync")

    after = InboxMessage.objects.filter(social_account__in=accounts).count()
    added = max(after - before, 0)

    if failures:
        messages.error(
            request,
            f"Instagram recovery sync had {len(failures)} error(s) ({'; '.join(failures)}). "
            f"{added} new inbox item(s) were added.",
        )
    elif added:
        messages.success(
            request,
            f"Instagram fast + deep recovery sync completed. {added} new inbox item(s) were added.",
        )
    else:
        messages.info(
            request,
            "Instagram fast + deep recovery sync completed successfully, but no new inbox items were returned.",
        )

    return redirect("inbox:instagram_health", workspace_id=workspace.id)
