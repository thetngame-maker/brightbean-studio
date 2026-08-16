"""Facebook production-readiness dashboard for the Unified Social Inbox."""

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone

from apps.members.decorators import require_permission
from apps.social_accounts.models import SocialAccount

from . import views
from .models import InboxMessage, InboxReply


@login_required
@require_permission("use_inbox")
def facebook_health(request, workspace_id):
    workspace = views._get_workspace(request, workspace_id)
    now = timezone.now()
    day_ago = now - timedelta(hours=24)
    week_ago = now - timedelta(days=7)

    accounts = list(
        SocialAccount.objects.filter(workspace=workspace, platform="facebook")
        .order_by("account_name", "connected_at")
    )

    account_rows = []
    ready_count = 0
    attention_count = 0

    for account in accounts:
        latest_message = (
            InboxMessage.objects.filter(social_account=account)
            .order_by("-received_at")
            .only("received_at", "message_type", "sender_name")
            .first()
        )
        latest_reply = (
            InboxReply.objects.filter(inbox_message__social_account=account)
            .exclude(platform_reply_id="")
            .order_by("-sent_at")
            .only("sent_at", "platform_reply_id")
            .first()
        )

        inbound_24h = InboxMessage.objects.filter(
            social_account=account,
            received_at__gte=day_ago,
        ).count()
        inbound_7d = InboxMessage.objects.filter(
            social_account=account,
            received_at__gte=week_ago,
        ).count()
        replies_7d = InboxReply.objects.filter(
            inbox_message__social_account=account,
            sent_at__gte=week_ago,
        ).exclude(platform_reply_id="").count()

        token_days = None
        token_state = "unknown"
        if account.token_expires_at:
            delta = account.token_expires_at - now
            token_days = delta.total_seconds() / 86400
            if token_days < 0:
                token_state = "expired"
            elif token_days <= 7:
                token_state = "expiring"
            else:
                token_state = "healthy"
        elif account.oauth_access_token:
            # Meta Page tokens may not expose a local expiry timestamp. A stored
            # token with no known expiry is valid enough for the readiness gate;
            # periodic health checks still catch provider-side invalidation.
            token_state = "present"
        else:
            token_state = "missing"

        issues = []
        warnings = []

        if account.connection_status in {
            SocialAccount.ConnectionStatus.DISCONNECTED,
            SocialAccount.ConnectionStatus.ERROR,
        }:
            issues.append("Account connection needs attention")
        elif account.connection_status == SocialAccount.ConnectionStatus.TOKEN_EXPIRING:
            warnings.append("Access token is marked as expiring")

        if token_state == "missing":
            issues.append("No access token is stored")
        elif token_state == "expired":
            issues.append("Access token is expired")
        elif token_state == "expiring":
            warnings.append("Access token expires within 7 days")

        if account.webhooks_active is False:
            issues.append("Facebook webhook subscription is inactive")
        elif account.webhooks_active is None:
            warnings.append("Webhook subscription has not been confirmed")

        if account.webhook_needs_reconnect:
            issues.append("Webhook permissions require reconnecting Facebook")
        elif account.webhook_error:
            warnings.append(account.webhook_error[:180])

        if account.last_error:
            warnings.append(account.last_error[:180])

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

        account_rows.append(
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
                "replies_7d": replies_7d,
            }
        )

    total_accounts = len(account_rows)
    instagram_gate_ready = bool(total_accounts and ready_count == total_accounts)

    context = {
        "workspace": workspace,
        "accounts": account_rows,
        "total_accounts": total_accounts,
        "ready_count": ready_count,
        "attention_count": attention_count,
        "instagram_gate_ready": instagram_gate_ready,
        "checked_at": now,
    }
    return render(request, "inbox/facebook_health.html", context)
