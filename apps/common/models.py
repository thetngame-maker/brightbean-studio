import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

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


class UGCCreator(models.Model):
    """One workspace-owned relationship record for a community creator."""

    class RelationshipStage(models.TextChoices):
        PROSPECT = "prospect", "Prospect"
        CONTACTED = "contacted", "Contacted"
        PERMISSIONED = "permissioned", "Permissioned"
        TRUSTED = "trusted", "Trusted creator"
        PARTNER = "partner", "Creator partner"
        DO_NOT_CONTACT = "do_not_contact", "Do not contact"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_creators",
    )
    display_name = models.CharField(max_length=255, blank=True, default="")
    relationship_stage = models.CharField(
        max_length=30,
        choices=RelationshipStage.choices,
        default=RelationshipStage.PROSPECT,
        db_index=True,
    )
    preferred_credit = models.CharField(max_length=255, blank=True, default="")
    tags = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_contacted_at = models.DateTimeField(null=True, blank=True)
    last_permission_granted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_creator"
        ordering = ["-last_seen_at", "display_name"]
        indexes = [
            models.Index(fields=["workspace", "relationship_stage", "-last_seen_at"], name="ugc_creator_stage_idx"),
            models.Index(fields=["workspace", "-last_seen_at"], name="ugc_creator_seen_idx"),
        ]

    def __str__(self):
        primary = self.identities.filter(is_primary=True).first() or self.identities.first()
        return self.display_name or (f"@{primary.handle}" if primary and primary.handle else "Community creator")


class UGCCreatorIdentity(models.Model):
    """A platform identity attached to one canonical creator relationship."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_creator_identities",
    )
    creator = models.ForeignKey(UGCCreator, on_delete=models.CASCADE, related_name="identities")
    platform = models.CharField(max_length=30, db_index=True)
    external_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    handle = models.CharField(max_length=255, blank=True, default="")
    normalized_handle = models.CharField(max_length=255, blank=True, default="", db_index=True)
    profile_url = models.URLField(max_length=2000, blank=True, default="")
    is_primary = models.BooleanField(default=True)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_creator_identity"
        ordering = ["-is_primary", "platform", "normalized_handle"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "platform", "normalized_handle"],
                condition=~models.Q(normalized_handle=""),
                name="ugc_identity_handle_uniq",
            ),
            models.UniqueConstraint(
                fields=["workspace", "platform", "external_id"],
                condition=~models.Q(external_id=""),
                name="ugc_identity_external_uniq",
            ),
        ]
        indexes = [models.Index(fields=["workspace", "platform", "normalized_handle"], name="ugc_identity_lookup_idx")]

    def __str__(self):
        return f"{self.platform}:@{self.handle or self.external_id}"


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
    creator = models.ForeignKey(
        UGCCreator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="submissions",
    )

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


class UGCRightsPassport(models.Model):
    """The current, auditable usage-rights state for one UGC asset."""

    class Status(models.TextChoices):
        NOT_REQUESTED = "not_requested", "Not requested"
        REQUESTED = "requested", "Requested"
        GRANTED = "granted", "Granted"
        DECLINED = "declined", "Declined"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_rights_passports",
    )
    submission = models.OneToOneField(
        UGCSubmission,
        on_delete=models.CASCADE,
        related_name="rights_passport",
    )
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.NOT_REQUESTED, db_index=True)
    allow_organic_social = models.BooleanField(default=False)
    allow_website = models.BooleanField(default=False)
    allow_email = models.BooleanField(default=False)
    allow_paid_ads = models.BooleanField(default=False)
    allow_print = models.BooleanField(default=False)
    allowed_account_ids = models.JSONField(default=list, blank=True)
    credit_required = models.BooleanField(default=True)
    credit_text = models.CharField(max_length=500, blank=True, default="")
    evidence_url = models.URLField(max_length=2000, blank=True, default="")
    evidence_note = models.TextField(blank=True, default="")
    consent_version = models.CharField(max_length=50, blank=True, default="")
    granted_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_ugc_rights_passports",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_rights_passport"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["workspace", "status", "-updated_at"], name="ugc_rights_status_idx"),
            models.Index(fields=["workspace", "expires_at"], name="ugc_rights_expiry_idx"),
        ]

    @property
    def is_active(self):
        if self.status != self.Status.GRANTED:
            return False
        return not self.expires_at or self.expires_at > timezone.now()

    @property
    def scope_labels(self):
        scopes = []
        for field, label in (
            ("allow_organic_social", "Organic social"),
            ("allow_website", "Website"),
            ("allow_email", "Email"),
            ("allow_paid_ads", "Paid ads"),
            ("allow_print", "Print"),
        ):
            if getattr(self, field):
                scopes.append(label)
        return scopes

    def __str__(self):
        return f"{self.get_status_display()} rights for {self.submission}"


class UGCCreatorCollaboration(models.Model):
    """A lightweight creator brief from invitation through rights-safe completion."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        INVITED = "invited", "Invited"
        INTERESTED = "interested", "Interested"
        CONFIRMED = "confirmed", "Confirmed"
        CONTENT_RECEIVED = "content_received", "Content received"
        COMPLETED = "completed", "Completed"
        DECLINED = "declined", "Declined"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_creator_collaborations",
    )
    creator = models.ForeignKey(
        UGCCreator,
        on_delete=models.CASCADE,
        related_name="collaborations",
    )
    submission = models.ForeignKey(
        UGCSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_collaborations",
    )
    title = models.CharField(max_length=255)
    brief = models.TextField(blank=True, default="")
    deliverables = models.TextField(blank=True, default="")
    offer = models.CharField(max_length=500, blank=True, default="")
    target_type = models.CharField(max_length=100, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default="")
    target_url = models.URLField(max_length=2000, blank=True, default="")
    requested_rights = models.JSONField(default=list, blank=True)
    invite_message = models.TextField(blank=True, default="")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT, db_index=True)
    content_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    invited_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ugc_creator_collaborations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_creator_collaboration"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["workspace", "status", "-updated_at"], name="ugc_collab_status_idx"),
            models.Index(fields=["creator", "status", "-updated_at"], name="ugc_collab_creator_idx"),
            models.Index(fields=["workspace", "content_due_at"], name="ugc_collab_due_idx"),
        ]

    @property
    def is_active(self):
        return self.status not in {self.Status.COMPLETED, self.Status.DECLINED, self.Status.CANCELLED}

    @property
    def is_overdue(self):
        return bool(self.is_active and self.content_due_at and self.content_due_at < timezone.now())

    def __str__(self):
        return self.title


class UGCCreatorTask(models.Model):
    """A lightweight next action in a workspace's creator relationship workflow."""

    class Kind(models.TextChoices):
        OUTREACH = "outreach", "Outreach"
        FOLLOW_UP = "follow_up", "Follow-up"
        THANK_YOU = "thank_you", "Thank-you"
        COLLABORATION = "collaboration", "Collaboration"
        RIGHTS_RENEWAL = "rights_renewal", "Rights renewal"
        CUSTOM = "custom", "Custom"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        DONE = "done", "Done"
        DISMISSED = "dismissed", "Dismissed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_creator_tasks",
    )
    creator = models.ForeignKey(
        UGCCreator,
        on_delete=models.CASCADE,
        related_name="tasks",
    )
    submission = models.ForeignKey(
        UGCSubmission,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="creator_tasks",
    )
    collaboration = models.ForeignKey(
        UGCCreatorCollaboration,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasks",
    )
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.CUSTOM, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    title = models.CharField(max_length=255)
    note = models.TextField(blank=True, default="")
    due_at = models.DateTimeField(db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ugc_creator_tasks",
    )
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_ugc_creator_tasks",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_creator_task"
        ordering = ["due_at", "created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "submission", "kind"],
                condition=models.Q(status="open", kind="rights_renewal"),
                name="ugc_ctask_open_renew_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "status", "due_at"], name="ugc_ctask_status_due_idx"),
            models.Index(fields=["creator", "status", "due_at"], name="ugc_ctask_creator_due_idx"),
        ]

    @property
    def is_overdue(self):
        return self.status == self.Status.OPEN and self.due_at < timezone.now()

    def __str__(self):
        return self.title


class UGCContentMission(models.Model):
    """A focused request for new community content around one canonical target."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="ugc_content_missions",
    )
    title = models.CharField(max_length=255)
    brief = models.TextField(blank=True, default="")
    deliverables = models.TextField(blank=True, default="")
    creator_prompt = models.TextField(blank=True, default="")
    offer = models.CharField(max_length=500, blank=True, default="")
    target_type = models.CharField(max_length=100, db_index=True)
    target_id = models.CharField(max_length=255, db_index=True)
    target_label = models.CharField(max_length=255)
    target_url = models.URLField(max_length=2000, blank=True, default="")
    goal_count = models.PositiveSmallIntegerField(default=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    starts_at = models.DateTimeField(default=timezone.now, db_index=True)
    due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_ugc_content_missions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_ugc_content_mission"
        ordering = ["due_at", "-updated_at"]
        indexes = [
            models.Index(fields=["workspace", "status", "due_at"], name="ugc_mission_status_due_idx"),
            models.Index(fields=["workspace", "target_type", "target_id"], name="ugc_mission_target_idx"),
        ]

    @property
    def is_overdue(self):
        return bool(self.status == self.Status.ACTIVE and self.due_at and self.due_at < timezone.now())

    def __str__(self):
        return self.title


class TourismGuardRule(models.Model):
    """Source-backed safety or accuracy guidance linked to a canonical TN target."""

    class Kind(models.TextChoices):
        SAFETY = "safety", "Safety"
        ACCESS = "access", "Access / closure"
        ACCURACY = "accuracy", "Fact accuracy"
        SEASONAL = "seasonal", "Seasonal conditions"
        ACCESSIBILITY = "accessibility", "Accessibility"
        SENSITIVE_LOCATION = "sensitive_location", "Sensitive location"

    class Severity(models.TextChoices):
        BLOCKER = "blocker", "Block publication"
        WARNING = "warning", "Warning"
        REMINDER = "reminder", "Reminder"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="tourism_guard_rules",
    )
    target_type = models.CharField(max_length=100, db_index=True)
    target_id = models.CharField(max_length=255, db_index=True)
    target_label = models.CharField(max_length=255)
    target_url = models.URLField(max_length=2000, blank=True, default="")
    kind = models.CharField(max_length=30, choices=Kind.choices, default=Kind.SAFETY, db_index=True)
    severity = models.CharField(max_length=20, choices=Severity.choices, default=Severity.WARNING, db_index=True)
    title = models.CharField(max_length=255)
    guidance = models.TextField()
    trigger_phrases = models.JSONField(default=list, blank=True)
    safe_context_phrases = models.JSONField(default=list, blank=True)
    source_url = models.URLField(max_length=2000)
    source_label = models.CharField(max_length=255, blank=True, default="")
    verified_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_tourism_guard_rules",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_tourism_guard_rules",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_tourism_guard_rule"
        ordering = ["target_label", "-severity", "title"]
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "target_type", "target_id", "title"],
                name="tour_guard_target_title_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["workspace", "is_active", "severity"], name="tour_guard_active_idx"),
            models.Index(fields=["workspace", "target_type", "target_id"], name="tour_guard_target_idx"),
        ]

    def __str__(self):
        return f"{self.target_label}: {self.title}"


class TourismGuardReview(models.Model):
    """Current human verification for one rule finding on one exact Post revision."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="tourism_guard_reviews",
    )
    post = models.ForeignKey(
        "composer.Post",
        on_delete=models.CASCADE,
        related_name="tourism_guard_reviews",
    )
    rule_key = models.CharField(max_length=100, db_index=True)
    finding_fingerprint = models.CharField(max_length=64, blank=True, default="", db_index=True)
    note = models.TextField(blank=True, default="")
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tourism_guard_reviews",
    )
    reviewed_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_tourism_guard_review"
        ordering = ["-reviewed_at"]
        constraints = [models.UniqueConstraint(fields=["workspace", "post", "rule_key"], name="tour_guard_review_uniq")]
        indexes = [
            models.Index(fields=["workspace", "post", "rule_key"], name="tour_guard_review_idx"),
        ]

    def __str__(self):
        return f"{self.post_id}: {self.rule_key}"


class ContentPerformanceProfile(models.Model):
    """Human teaching labels that turn post analytics into reusable lessons."""

    class SourceType(models.TextChoices):
        UGC = "ugc", "Community / UGC"
        BRANDED = "branded", "Branded"
        PARTNER = "partner", "Partner"
        MIXED = "mixed", "Mixed"

    class OpeningHook(models.TextChoices):
        SCENIC_REVEAL = "scenic_reveal", "Scenic reveal"
        PERSON_ON_CAMERA = "person_on_camera", "Person on camera"
        ACTION_FIRST = "action_first", "Action first"
        TEXT_TEASER = "text_teaser", "Text teaser"
        QUESTION = "question", "Question"
        DETAIL_CLOSEUP = "detail_closeup", "Detail close-up"
        OTHER = "other", "Other"

    class CaptionStyle(models.TextChoices):
        SHORT = "short", "Short"
        STORY = "story", "Story"
        GUIDE = "guide", "Useful guide"
        LIST = "list", "List"
        QUESTION = "question", "Question-led"
        ANNOUNCEMENT = "announcement", "Announcement"
        OTHER = "other", "Other"

    class Season(models.TextChoices):
        SPRING = "spring", "Spring"
        SUMMER = "summer", "Summer"
        FALL = "fall", "Fall"
        WINTER = "winter", "Winter"
        EVERGREEN = "evergreen", "Evergreen"

    class Subject(models.TextChoices):
        WATERFALL = "waterfall", "Waterfall"
        TRAIL = "trail", "Trail"
        SCENIC = "scenic", "Scenic place"
        EVENT = "event", "Event"
        FOOD = "food", "Food"
        HISTORY = "history", "History"
        WILDLIFE = "wildlife", "Wildlife"
        PEOPLE = "people", "People"
        COMMUNITY = "community", "Community"
        OTHER = "other", "Other"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="content_performance_profiles",
    )
    post = models.OneToOneField(
        "composer.Post",
        on_delete=models.CASCADE,
        related_name="performance_profile",
    )
    source_submission = models.ForeignKey(
        UGCSubmission,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_profiles",
    )
    creator = models.ForeignKey(
        UGCCreator,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="performance_profiles",
    )
    source_type = models.CharField(max_length=20, choices=SourceType.choices, blank=True, default="", db_index=True)
    opening_hook = models.CharField(
        max_length=30,
        choices=OpeningHook.choices,
        blank=True,
        default="",
        db_index=True,
    )
    caption_style = models.CharField(
        max_length=30,
        choices=CaptionStyle.choices,
        blank=True,
        default="",
        db_index=True,
    )
    season = models.CharField(max_length=20, choices=Season.choices, blank=True, default="", db_index=True)
    subject = models.CharField(max_length=30, choices=Subject.choices, blank=True, default="", db_index=True)
    target_type = models.CharField(max_length=100, blank=True, default="", db_index=True)
    target_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    target_label = models.CharField(max_length=255, blank=True, default="")
    target_url = models.URLField(max_length=2000, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_content_performance_profiles",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_content_performance_profiles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = WorkspaceScopedManager()

    class Meta:
        db_table = "common_content_performance_profile"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["workspace", "source_type"], name="content_perf_source_idx"),
            models.Index(fields=["workspace", "target_type", "target_id"], name="content_perf_target_idx"),
        ]

    def __str__(self):
        return f"Learning profile for {self.post_id}"


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
