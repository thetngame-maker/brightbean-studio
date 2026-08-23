import decimal
import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0008_tourism_guard"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TourismImpactReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("period_start", models.DateField(db_index=True)),
                ("period_end", models.DateField(db_index=True)),
                ("target_type", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("target_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("draft", "Internal draft"),
                            ("shared", "Shared with partners"),
                            ("archived", "Archived"),
                        ],
                        db_index=True,
                        default="draft",
                        max_length=20,
                    ),
                ),
                ("snapshot", models.JSONField(default=dict)),
                ("partner_notes", models.TextField(blank=True, default="")),
                ("website_visits", models.PositiveIntegerField(blank=True, null=True)),
                ("registrations", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "equivalent_cpm",
                    models.DecimalField(decimal_places=2, default=decimal.Decimal("12"), max_digits=8),
                ),
                ("generated_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("shared_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "generated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="generated_tourism_impact_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "shared_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shared_tourism_impact_reports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tourism_impact_reports",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_tourism_impact_report",
                "ordering": ["-period_end", "-generated_at"],
                "indexes": [
                    models.Index(fields=["workspace", "status", "-period_end"], name="impact_report_status_idx"),
                    models.Index(fields=["workspace", "target_type", "target_id"], name="impact_report_target_idx"),
                ],
            },
        ),
    ]
