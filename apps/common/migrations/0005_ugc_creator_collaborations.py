import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0004_ugc_creator_tasks"),
        ("workspaces", "0004_workspace_discovery_searches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCCreatorCollaboration",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("brief", models.TextField(blank=True, default="")),
                ("deliverables", models.TextField(blank=True, default="")),
                ("offer", models.CharField(blank=True, default="", max_length=500)),
                ("target_type", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("target_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                ("requested_rights", models.JSONField(blank=True, default=list)),
                ("invite_message", models.TextField(blank=True, default="")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("invited", "Invited"),
                            ("interested", "Interested"),
                            ("confirmed", "Confirmed"),
                            ("content_received", "Content received"),
                            ("completed", "Completed"),
                            ("declined", "Declined"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=30,
                    ),
                ),
                ("content_due_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("invited_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_ugc_creator_collaborations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="collaborations",
                        to="common.ugccreator",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="creator_collaborations",
                        to="common.ugcsubmission",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_creator_collaborations",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_ugc_creator_collaboration",
                "ordering": ["-updated_at"],
            },
        ),
        migrations.AddField(
            model_name="ugccreatortask",
            name="collaboration",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="common.ugccreatorcollaboration",
            ),
        ),
        migrations.AddIndex(
            model_name="ugccreatorcollaboration",
            index=models.Index(fields=["workspace", "status", "-updated_at"], name="ugc_collab_status_idx"),
        ),
        migrations.AddIndex(
            model_name="ugccreatorcollaboration",
            index=models.Index(fields=["creator", "status", "-updated_at"], name="ugc_collab_creator_idx"),
        ),
        migrations.AddIndex(
            model_name="ugccreatorcollaboration",
            index=models.Index(fields=["workspace", "content_due_at"], name="ugc_collab_due_idx"),
        ),
    ]
