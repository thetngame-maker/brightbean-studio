import uuid

from django.conf import settings
from django.db import models


class RouteVideoRenderJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RENDERING = "rendering", "Rendering"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        "workspaces.Workspace",
        on_delete=models.CASCADE,
        related_name="route_video_jobs",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="route_video_jobs",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    manifest = models.JSONField(default=dict)
    total_clips = models.PositiveIntegerField(default=0)
    completed_clips = models.PositiveIntegerField(default=0)
    current_clip_label = models.CharField(max_length=255, blank=True, default="")
    media_asset_ids = models.JSONField(default=list, blank=True)
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "route_videos_render_job"
        ordering = ["-created_at"]
