import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedManager


class AuditEvent(models.Model):
    """Immutable, workspace-scoped record of meaningful product actions.

    UGC moderation, inbox operations, publishing, approvals, and future admin
    tools can all write into this one trail.  Keep metadata descriptive but
    never store secrets, OAuth tokens, or full sensitive request payloads.
    """

    class Source(models.TextChoices):
        UI = "ui", "User interface"
        API = "api", "API"
        SYSTEM = "system", "System"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="audit_events",
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    action = models.CharField(max_length=100, db_index=True)
    target_type = models.CharField(max_length=100, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default="")
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.UI)
    metadata = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_audit_event"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "-created_at"], name="audit_ws_created_idx"),
            models.Index(fields=["workspace", "action", "-created_at"], name="audit_ws_action_idx"),
            models.Index(fields=["workspace", "target_type", "target_id"], name="audit_ws_target_idx"),
        ]

    def __str__(self):
        actor = self.actor_id or "system"
        return f"{self.action} by {actor}"
