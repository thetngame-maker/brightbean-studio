import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import apps.common.encryption


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0011_campaign_attribution"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCCreatorRightsRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("request_token", apps.common.encryption.EncryptedTextField()),
                ("token_hash", models.CharField(db_index=True, max_length=64, unique=True)),
                ("token_hint", models.CharField(blank=True, default="", max_length=8)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Awaiting creator"),
                            ("granted", "Granted by creator"),
                            ("declined", "Declined by creator"),
                            ("superseded", "Replaced"),
                            ("cancelled", "Cancelled"),
                            ("expired", "Expired"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("consent_version", models.CharField(default="creator-rights-portal-v1", max_length=50)),
                ("allow_organic_social", models.BooleanField(default=True)),
                ("allow_website", models.BooleanField(default=True)),
                ("allow_email", models.BooleanField(default=False)),
                ("allow_paid_ads", models.BooleanField(default=False)),
                ("allow_print", models.BooleanField(default=False)),
                ("credit_required", models.BooleanField(default=True)),
                ("credit_text", models.CharField(blank=True, default="", max_length=500)),
                ("granted_scopes", models.JSONField(blank=True, default=list)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("responded_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_ugc_creator_rights_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "submission",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="creator_rights_requests",
                        to="common.ugcsubmission",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_creator_rights_requests",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_ugc_creator_rights_request",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        fields=["workspace", "status", "expires_at"],
                        name="ugc_rights_req_status_idx",
                    ),
                    models.Index(fields=["submission", "-created_at"], name="ugc_rights_req_sub_idx"),
                ],
            },
        ),
    ]
