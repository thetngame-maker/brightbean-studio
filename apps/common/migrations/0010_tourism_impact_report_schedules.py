import decimal
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0009_tourism_impact_reports"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TourismImpactReportSchedule",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                (
                    "cadence",
                    models.CharField(
                        choices=[("weekly", "Weekly"), ("monthly", "Monthly"), ("quarterly", "Quarterly")],
                        db_index=True,
                        default="monthly",
                        max_length=20,
                    ),
                ),
                ("target_type", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("target_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                (
                    "equivalent_cpm",
                    models.DecimalField(decimal_places=2, default=decimal.Decimal("12"), max_digits=8),
                ),
                ("auto_share", models.BooleanField(default=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("next_run_at", models.DateTimeField(db_index=True)),
                ("last_run_at", models.DateTimeField(blank=True, null=True)),
                ("last_period_end", models.DateField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=500)),
                ("archived_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_tourism_impact_report_schedules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_tourism_impact_report_schedules",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tourism_impact_report_schedules",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_tourism_impact_report_schedule",
                "ordering": ["-is_active", "next_run_at", "name"],
                "indexes": [
                    models.Index(fields=["is_active", "next_run_at"], name="impact_schedule_due_idx"),
                    models.Index(fields=["workspace", "archived_at"], name="impact_schedule_ws_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="tourismimpactreport",
            name="source_schedule",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="reports",
                to="common.tourismimpactreportschedule",
            ),
        ),
        migrations.AddConstraint(
            model_name="tourismimpactreport",
            constraint=models.UniqueConstraint(
                fields=("source_schedule", "period_start", "period_end"),
                name="impact_report_schedule_period_uniq",
            ),
        ),
    ]
