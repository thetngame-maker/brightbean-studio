import uuid
from datetime import timedelta

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def backfill_rights_renewal_tasks(apps, schema_editor):
    passport_model = apps.get_model("common", "UGCRightsPassport")
    task_model = apps.get_model("common", "UGCCreatorTask")
    now = timezone.now()
    passports = passport_model.objects.filter(status="granted", expires_at__isnull=False).select_related("submission")
    for passport in passports.iterator(chunk_size=200):
        creator_id = passport.submission.creator_id
        if not creator_id:
            continue
        content_label = passport.submission.title or passport.submission.target_label or "community content"
        task_model.objects.create(
            workspace_id=passport.workspace_id,
            creator_id=creator_id,
            submission_id=passport.submission_id,
            kind="rights_renewal",
            status="open",
            title=f"Renew rights for {content_label}"[:255],
            note=f"Creator permission expires {passport.expires_at.date().isoformat()}.",
            due_at=max(now, passport.expires_at - timedelta(days=14)),
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0003_ugc_creator_relationships_and_rights"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCCreatorTask",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("outreach", "Outreach"),
                            ("follow_up", "Follow-up"),
                            ("thank_you", "Thank-you"),
                            ("collaboration", "Collaboration"),
                            ("rights_renewal", "Rights renewal"),
                            ("custom", "Custom"),
                        ],
                        db_index=True,
                        default="custom",
                        max_length=30,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("open", "Open"), ("done", "Done"), ("dismissed", "Dismissed")],
                        db_index=True,
                        default="open",
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("note", models.TextField(blank=True, default="")),
                ("due_at", models.DateTimeField(db_index=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "completed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="completed_ugc_creator_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_ugc_creator_tasks",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tasks",
                        to="common.ugccreator",
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="creator_tasks",
                        to="common.ugcsubmission",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_creator_tasks",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={"db_table": "common_ugc_creator_task", "ordering": ["due_at", "created_at"]},
        ),
        migrations.AddIndex(
            model_name="ugccreatortask",
            index=models.Index(fields=["workspace", "status", "due_at"], name="ugc_ctask_status_due_idx"),
        ),
        migrations.AddIndex(
            model_name="ugccreatortask",
            index=models.Index(fields=["creator", "status", "due_at"], name="ugc_ctask_creator_due_idx"),
        ),
        migrations.AddConstraint(
            model_name="ugccreatortask",
            constraint=models.UniqueConstraint(
                condition=models.Q(kind="rights_renewal", status="open"),
                fields=("workspace", "submission", "kind"),
                name="ugc_ctask_open_renew_uniq",
            ),
        ),
        migrations.RunPython(backfill_rights_renewal_tasks, noop_reverse),
    ]
