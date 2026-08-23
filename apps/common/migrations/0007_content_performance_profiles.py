import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0006_ugc_content_missions"),
        ("composer", "0020_platformpost_first_comment_state"),
        ("workspaces", "0004_workspace_discovery_searches"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ContentPerformanceProfile",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "source_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("ugc", "Community / UGC"),
                            ("branded", "Branded"),
                            ("partner", "Partner"),
                            ("mixed", "Mixed"),
                        ],
                        db_index=True,
                        default="",
                        max_length=20,
                    ),
                ),
                (
                    "opening_hook",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("scenic_reveal", "Scenic reveal"),
                            ("person_on_camera", "Person on camera"),
                            ("action_first", "Action first"),
                            ("text_teaser", "Text teaser"),
                            ("question", "Question"),
                            ("detail_closeup", "Detail close-up"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="",
                        max_length=30,
                    ),
                ),
                (
                    "caption_style",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("short", "Short"),
                            ("story", "Story"),
                            ("guide", "Useful guide"),
                            ("list", "List"),
                            ("question", "Question-led"),
                            ("announcement", "Announcement"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="",
                        max_length=30,
                    ),
                ),
                (
                    "season",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("spring", "Spring"),
                            ("summer", "Summer"),
                            ("fall", "Fall"),
                            ("winter", "Winter"),
                            ("evergreen", "Evergreen"),
                        ],
                        db_index=True,
                        default="",
                        max_length=20,
                    ),
                ),
                (
                    "subject",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("waterfall", "Waterfall"),
                            ("trail", "Trail"),
                            ("scenic", "Scenic place"),
                            ("event", "Event"),
                            ("food", "Food"),
                            ("history", "History"),
                            ("wildlife", "Wildlife"),
                            ("people", "People"),
                            ("community", "Community"),
                            ("other", "Other"),
                        ],
                        db_index=True,
                        default="",
                        max_length=30,
                    ),
                ),
                ("target_type", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("target_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                ("notes", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "creator",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="performance_profiles",
                        to="common.ugccreator",
                    ),
                ),
                (
                    "post",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="performance_profile",
                        to="composer.post",
                    ),
                ),
                (
                    "source_submission",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="performance_profiles",
                        to="common.ugcsubmission",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="content_performance_profiles",
                        to="workspaces.workspace",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_content_performance_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_content_performance_profiles",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"db_table": "common_content_performance_profile", "ordering": ["-updated_at"]},
        ),
        migrations.AddIndex(
            model_name="contentperformanceprofile",
            index=models.Index(fields=["workspace", "source_type"], name="content_perf_source_idx"),
        ),
        migrations.AddIndex(
            model_name="contentperformanceprofile",
            index=models.Index(fields=["workspace", "target_type", "target_id"], name="content_perf_target_idx"),
        ),
    ]
