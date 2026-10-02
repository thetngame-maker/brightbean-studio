# Generated for Route Videos.
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("workspaces", "0005_workspace_community_smart_rules"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="RouteVideoRenderJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("status", models.CharField(choices=[("queued", "Queued"), ("rendering", "Rendering"), ("completed", "Completed"), ("failed", "Failed")], default="queued", max_length=20)),
                ("manifest", models.JSONField(default=dict)),
                ("total_clips", models.PositiveIntegerField(default=0)),
                ("completed_clips", models.PositiveIntegerField(default=0)),
                ("current_clip_label", models.CharField(blank=True, default="", max_length=255)),
                ("media_asset_ids", models.JSONField(blank=True, default=list)),
                ("error_message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="route_video_jobs", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="route_video_jobs", to="workspaces.workspace")),
            ],
            options={"db_table": "route_videos_render_job", "ordering": ["-created_at"]},
        ),
    ]
