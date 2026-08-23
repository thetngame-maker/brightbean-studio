import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import apps.common.models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0010_tourism_impact_report_schedules"),
        ("composer", "0020_platformpost_first_comment_state"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CampaignAttributionLink",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "code",
                    models.CharField(
                        default=apps.common.models._generate_attribution_code,
                        editable=False,
                        max_length=16,
                        unique=True,
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("destination_url", models.URLField(max_length=2000)),
                ("target_type", models.CharField(blank=True, db_index=True, default="", max_length=100)),
                ("target_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                ("utm_source", models.CharField(blank=True, default="social", max_length=100)),
                ("utm_medium", models.CharField(blank=True, default="organic", max_length=100)),
                ("utm_campaign", models.CharField(blank=True, default="", max_length=150)),
                ("conversion_secret_hash", models.CharField(max_length=64)),
                ("conversion_secret_hint", models.CharField(blank=True, default="", max_length=12)),
                ("click_count", models.PositiveBigIntegerField(default=0)),
                ("unique_visitor_count", models.PositiveBigIntegerField(default=0)),
                ("registration_count", models.PositiveBigIntegerField(default=0)),
                ("last_clicked_at", models.DateTimeField(blank=True, null=True)),
                ("last_converted_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("archived_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_campaign_attribution_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "post",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="campaign_attribution_links",
                        to="composer.post",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="updated_campaign_attribution_links",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="campaign_attribution_links",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_campaign_attribution_link",
                "ordering": ["-is_active", "-updated_at"],
                "indexes": [
                    models.Index(fields=["workspace", "is_active", "-updated_at"], name="attrib_link_active_idx"),
                    models.Index(fields=["workspace", "target_type", "target_id"], name="attrib_link_target_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CampaignAttributionClick",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("day", models.DateField(db_index=True)),
                ("visitor_hash", models.CharField(max_length=64)),
                ("clicks", models.PositiveIntegerField(default=1)),
                ("first_clicked_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("last_clicked_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="click_days",
                        to="common.campaignattributionlink",
                    ),
                ),
            ],
            options={
                "db_table": "common_campaign_attribution_click",
                "ordering": ["-day", "-last_clicked_at"],
                "indexes": [models.Index(fields=["link", "day"], name="attrib_click_link_day_idx")],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("link", "day", "visitor_hash"), name="attrib_click_daily_visitor_uniq"
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="CampaignAttributionConversion",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "source",
                    models.CharField(
                        choices=[("webhook", "TN Game webhook"), ("manual", "Manual entry")], max_length=20
                    ),
                ),
                ("external_id_hash", models.CharField(max_length=64)),
                ("external_id_hint", models.CharField(blank=True, default="", max_length=12)),
                ("quantity", models.PositiveIntegerField(default=1)),
                ("occurred_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("note", models.CharField(blank=True, default="", max_length=500)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "link",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conversions",
                        to="common.campaignattributionlink",
                    ),
                ),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_campaign_attribution_conversions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "common_campaign_attribution_conversion",
                "ordering": ["-occurred_at", "-created_at"],
                "indexes": [
                    models.Index(fields=["link", "occurred_at"], name="attrib_conv_link_time_idx")
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("link", "external_id_hash"), name="attrib_conversion_external_uniq"
                    )
                ],
            },
        ),
    ]
