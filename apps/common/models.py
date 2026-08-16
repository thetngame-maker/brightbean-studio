import uuid

from django.conf import settings
from django.db import models

from apps.common.managers import WorkspaceScopedManager


class AuditEvent(models.Model):
    """Immutable, workspace-scoped record of meaningful product actions.

    UGC moderation, inbox operations, publishing, approvals, and future admin
    tools can all write into this one trail. Keep metadata descriptive but
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


class UGCSubmission(models.Model):
    """One piece of community-contributed content awaiting or past moderation.

    Targets are intentionally generic so Studio can moderate content belonging
    to TN Game entities that do not live in this database (Top Sights, trails,
    events, restaurants, etc.). Photos reuse MediaAsset rather than creating a
    second storage pipeline.
    """

    class Kind(models.TextChoices):
        PHOTO = "photo", "Photo"
        REVIEW = "review", "Review"
        TIP = "tip", "Tip"
        TRAIL_REPORT = "trail_report", "Trail report"
        COMMUNITY_POST = "community_post", "Community post"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REMOVED = "removed", "Removed"

    class Source(models.TextChoices):
        UI = "ui", "User interface"
        API = "api", "API"
        WEBHOOK = "webhook", "Webhook"
        IMPORT = "import", "Import"

    class Attribution(models.TextChoices):
        NAME = "name", "Display name"
        HANDLE = "handle", "Handle"
        ANONYMOUS = "anonymous", "Anonymous"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_submissions",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.UI)

    contributor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ugc_submissions",
    )
    contributor_external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    contributor_name = models.CharField(max_length=255, blank=True, default="")
    contributor_handle = models.CharField(max_length=255, blank=True, default="")
    attribution = models.CharField(max_length=20, choices=Attribution.choices, default=Attribution.NAME)

    target_type = models.CharField(max_length=100, db_index=True)
    target_id = models.CharField(max_length=255, db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default="")
    target_url = models.URLField(max_length=2000, blank=True, default="")

    media_asset = models.ForeignKey(
        "media_library.MediaAsset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ugc_submissions",
    )
    title = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField(blank=True, default="")
    rating = models.PositiveSmallIntegerField(null=True, blank=True)

    consent_confirmed = models.BooleanField(default=False)
    consent_version = models.CharField(max_length=50, blank=True, default="")
    consent_at = models.DateTimeField(null=True, blank=True)

    moderated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="moderated_ugc_submissions",
    )
    moderated_at = models.DateTimeField(null=True, blank=True)
    moderation_note = models.TextField(blank=True, default="")
    published_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    submitted_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_submission"
        ordering = ["-submitted_at"]
        indexes = [
            models.Index(fields=["workspace", "status", "-submitted_at"], name="ugc_ws_status_idx"),
            models.Index(fields=["workspace", "kind", "-submitted_at"], name="ugc_ws_kind_idx"),
            models.Index(fields=["workspace", "target_type", "target_id"], name="ugc_ws_target_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__isnull=True) | (models.Q(rating__gte=1) & models.Q(rating__lte=5)),
                name="ugc_rating_1_to_5",
            ),
        ]

    def __str__(self):
        return self.title or self.target_label or f"{self.get_kind_display()} {self.id}"


class UGCModerationEvent(models.Model):
    """Append-only history of moderator decisions for a UGC submission."""

    class Action(models.TextChoices):
        APPROVE = "approve", "Approve"
        REJECT = "reject", "Reject"
        REMOVE = "remove", "Remove"
        RESTORE = "restore", "Restore"
        NOTE = "note", "Note"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    submission = models.ForeignKey(UGCSubmission, on_delete=models.CASCADE, related_name="moderation_events")
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ugc_moderation_events",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    from_status = models.CharField(max_length=20, choices=UGCSubmission.Status.choices)
    to_status = models.CharField(max_length=20, choices=UGCSubmission.Status.choices)
    note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "common_ugc_moderation_event"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["submission", "-created_at"], name="ugc_mod_submission_idx")]


class UGCReport(models.Model):
    """A user or external-community report against a UGC submission."""

    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        INAPPROPRIATE = "inappropriate", "Inappropriate content"
        INCORRECT = "incorrect", "Incorrect information"
        COPYRIGHT = "copyright", "Copyright"
        PRIVACY = "privacy", "Privacy"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        REVIEWING = "reviewing", "Reviewing"
        RESOLVED = "resolved", "Resolved"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_reports",
    )
    submission = models.ForeignKey(UGCSubmission, on_delete=models.CASCADE, related_name="reports")
    reporter = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ugc_reports",
    )
    reporter_external_id = models.CharField(max_length=255, blank=True, default="")
    reporter_name = models.CharField(max_length=255, blank=True, default="")
    reason = models.CharField(max_length=30, choices=Reason.choices, db_index=True)
    details = models.TextField(blank=True, default="")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="handled_ugc_reports",
    )
    handled_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_report"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["workspace", "status", "-created_at"], name="ugc_report_ws_status_idx"),
            models.Index(fields=["submission", "status"], name="ugc_report_submission_idx"),
        ]
