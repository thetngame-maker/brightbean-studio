import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0007_content_performance_profiles"),
        ("composer", "0020_platformpost_first_comment_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TourismGuardRule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("target_type", models.CharField(db_index=True, max_length=100)),
                ("target_id", models.CharField(db_index=True, max_length=255)),
                ("target_label", models.CharField(max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("safety", "Safety"),
                            ("access", "Access / closure"),
                            ("accuracy", "Fact accuracy"),
                            ("seasonal", "Seasonal conditions"),
                            ("accessibility", "Accessibility"),
                            ("sensitive_location", "Sensitive location"),
                        ],
                        db_index=True,
                        default="safety",
                        max_length=30,
                    ),
                ),
                (
                    "severity",
                    models.CharField(
                        choices=[
                            ("blocker", "Block publication"),
                            ("warning", "Warning"),
                            ("reminder", "Reminder"),
                        ],
                        db_index=True,
                        default="warning",
                        max_length=20,
                    ),
                ),
                ("title", models.CharField(max_length=255)),
                ("guidance", models.TextField()),
                ("trigger_phrases", models.JSONField(blank=True, default=list)),
                ("safe_context_phrases", models.JSONField(blank=True, default=list)),
                ("source_url", models.URLField(max_length=2000)),
                ("source_label", models.CharField(blank=True, default="", max_length=255)),
                ("verified_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_tourism_guard_rules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_tourism_guard_rules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tourism_guard_rules",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={"db_table": "common_tourism_guard_rule", "ordering": ["target_label", "-severity", "title"]},
        ),
        migrations.CreateModel(
            name="TourismGuardReview",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("rule_key", models.CharField(db_index=True, max_length=100)),
                ("finding_fingerprint", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("note", models.TextField(blank=True, default="")),
                ("reviewed_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tourism_guard_reviews",
                        to="composer.post",
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="tourism_guard_reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tourism_guard_reviews",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={"db_table": "common_tourism_guard_review", "ordering": ["-reviewed_at"]},
        ),
        migrations.AddConstraint(
            model_name="tourismguardrule",
            constraint=models.UniqueConstraint(
                fields=("workspace", "target_type", "target_id", "title"),
                name="tour_guard_target_title_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="tourismguardrule",
            index=models.Index(fields=["workspace", "is_active", "severity"], name="tour_guard_active_idx"),
        ),
        migrations.AddIndex(
            model_name="tourismguardrule",
            index=models.Index(fields=["workspace", "target_type", "target_id"], name="tour_guard_target_idx"),
        ),
        migrations.AddConstraint(
            model_name="tourismguardreview",
            constraint=models.UniqueConstraint(
                fields=("workspace", "post", "rule_key"),
                name="tour_guard_review_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="tourismguardreview",
            index=models.Index(fields=["workspace", "post", "rule_key"], name="tour_guard_review_idx"),
        ),
    ]
