import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0005_ugc_creator_collaborations"),
        ("workspaces", "0004_workspace_discovery_searches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCContentMission",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("brief", models.TextField(blank=True, default="")),
                ("deliverables", models.TextField(blank=True, default="")),
                ("creator_prompt", models.TextField(blank=True, default="")),
                ("offer", models.CharField(blank=True, default="", max_length=500)),
                ("target_type", models.CharField(db_index=True, max_length=100)),
                ("target_id", models.CharField(db_index=True, max_length=255)),
                ("target_label", models.CharField(max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                ("goal_count", models.PositiveSmallIntegerField(default=3)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("active", "Active"),
                            ("paused", "Paused"),
                            ("completed", "Completed"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("starts_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("due_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_ugc_content_missions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_content_missions",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_ugc_content_mission",
                "ordering": ["due_at", "-updated_at"],
            },
        ),
        migrations.AddIndex(
            model_name="ugccontentmission",
            index=models.Index(fields=["workspace", "status", "due_at"], name="ugc_mission_status_due_idx"),
        ),
        migrations.AddIndex(
            model_name="ugccontentmission",
            index=models.Index(fields=["workspace", "target_type", "target_id"], name="ugc_mission_target_idx"),
        ),
    ]
