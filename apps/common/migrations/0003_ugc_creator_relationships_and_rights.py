import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def backfill_creator_relationships_and_rights(apps, schema_editor):
    creator_model = apps.get_model("common", "UGCCreator")
    identity_model = apps.get_model("common", "UGCCreatorIdentity")
    passport_model = apps.get_model("common", "UGCRightsPassport")
    submission_model = apps.get_model("common", "UGCSubmission")

    for submission in submission_model.objects.all().iterator(chunk_size=200):
        metadata = submission.metadata if isinstance(submission.metadata, dict) else {}
        provenance = metadata.get("provenance") if isinstance(metadata.get("provenance"), dict) else {}
        permission = metadata.get("permission") if isinstance(metadata.get("permission"), dict) else {}
        platform = str(provenance.get("platform") or "direct").strip().lower()[:30] or "direct"
        handle = str(submission.contributor_handle or provenance.get("creator_handle") or "").strip().lstrip("@")[:255]
        normalized_handle = handle.lower()
        external_id = str(submission.contributor_external_id or "").strip()[:255]
        creator = None
        identity = None
        identities = identity_model.objects.filter(workspace_id=submission.workspace_id, platform=platform)
        if external_id:
            identity = identities.filter(external_id=external_id).first()
        if identity is None and normalized_handle:
            identity = identities.filter(normalized_handle=normalized_handle).first()
        if identity:
            creator = identity.creator
        elif normalized_handle or external_id:
            seen_at = submission.submitted_at or timezone.now()
            creator = creator_model.objects.create(
                workspace_id=submission.workspace_id,
                display_name=str(submission.contributor_name or "").strip()[:255],
                preferred_credit=f"@{handle}" if handle else str(submission.contributor_name or "").strip()[:255],
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
            profile_url = f"https://www.instagram.com/{handle}/" if platform == "instagram" and handle else ""
            identity_model.objects.create(
                workspace_id=submission.workspace_id,
                creator=creator,
                platform=platform,
                external_id=external_id,
                handle=handle,
                normalized_handle=normalized_handle,
                profile_url=profile_url,
                is_primary=True,
                first_seen_at=seen_at,
                last_seen_at=seen_at,
            )
        if creator:
            submission_model.objects.filter(id=submission.id).update(creator_id=creator.id)
            seen_at = submission.submitted_at or timezone.now()
            update_fields = []
            if seen_at < creator.first_seen_at:
                creator.first_seen_at = seen_at
                update_fields.append("first_seen_at")
            if seen_at > creator.last_seen_at:
                creator.last_seen_at = seen_at
                update_fields.append("last_seen_at")
            if submission.consent_confirmed:
                creator.relationship_stage = "permissioned"
                creator.last_permission_granted_at = submission.consent_at
                update_fields.extend(["relationship_stage", "last_permission_granted_at"])
            elif (
                str(permission.get("status") or "") in {"requested", "declined"}
                and creator.relationship_stage == "prospect"
            ):
                creator.relationship_stage = "contacted"
                update_fields.append("relationship_stage")
            if update_fields:
                creator.save(update_fields=list(dict.fromkeys(update_fields)))

        permission_status = str(permission.get("status") or "not_contacted").strip().lower()
        if submission.consent_confirmed or permission_status == "granted":
            rights_status = "granted"
        elif permission_status == "requested":
            rights_status = "requested"
        elif permission_status == "declined":
            rights_status = "declined"
        else:
            rights_status = "not_requested"
        passport_model.objects.create(
            workspace_id=submission.workspace_id,
            submission_id=submission.id,
            status=rights_status,
            allow_organic_social=bool(submission.consent_confirmed),
            allow_website=bool(submission.consent_confirmed),
            credit_required=True,
            credit_text=f"@{handle}" if handle else str(submission.contributor_name or "").strip()[:500],
            evidence_url=str(provenance.get("source_url") or "").strip()[:2000],
            consent_version=submission.consent_version,
            granted_at=submission.consent_at if submission.consent_confirmed else None,
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0002_ugc_foundation"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCCreator",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("display_name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "relationship_stage",
                    models.CharField(
                        choices=[
                            ("prospect", "Prospect"),
                            ("contacted", "Contacted"),
                            ("permissioned", "Permissioned"),
                            ("trusted", "Trusted creator"),
                            ("partner", "Creator partner"),
                            ("do_not_contact", "Do not contact"),
                        ],
                        db_index=True,
                        default="prospect",
                        max_length=30,
                    ),
                ),
                ("preferred_credit", models.CharField(blank=True, default="", max_length=255)),
                ("tags", models.JSONField(blank=True, default=list)),
                ("notes", models.TextField(blank=True, default="")),
                ("first_seen_at", models.DateTimeField(default=timezone.now)),
                ("last_seen_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("last_contacted_at", models.DateTimeField(blank=True, null=True)),
                ("last_permission_granted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_creators",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={"db_table": "common_ugc_creator", "ordering": ["-last_seen_at", "display_name"]},
        ),
        migrations.CreateModel(
            name="UGCCreatorIdentity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("platform", models.CharField(db_index=True, max_length=30)),
                ("external_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("handle", models.CharField(blank=True, default="", max_length=255)),
                ("normalized_handle", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("profile_url", models.URLField(blank=True, default="", max_length=2000)),
                ("is_primary", models.BooleanField(default=True)),
                ("first_seen_at", models.DateTimeField(default=timezone.now)),
                ("last_seen_at", models.DateTimeField(default=timezone.now)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "creator",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="identities", to="common.ugccreator"
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_creator_identities",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "common_ugc_creator_identity",
                "ordering": ["-is_primary", "platform", "normalized_handle"],
            },
        ),
        migrations.AddField(
            model_name="ugcsubmission",
            name="creator",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="submissions",
                to="common.ugccreator",
            ),
        ),
        migrations.CreateModel(
            name="UGCRightsPassport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("not_requested", "Not requested"),
                            ("requested", "Requested"),
                            ("granted", "Granted"),
                            ("declined", "Declined"),
                            ("revoked", "Revoked"),
                            ("expired", "Expired"),
                        ],
                        db_index=True,
                        default="not_requested",
                        max_length=30,
                    ),
                ),
                ("allow_organic_social", models.BooleanField(default=False)),
                ("allow_website", models.BooleanField(default=False)),
                ("allow_email", models.BooleanField(default=False)),
                ("allow_paid_ads", models.BooleanField(default=False)),
                ("allow_print", models.BooleanField(default=False)),
                ("allowed_account_ids", models.JSONField(blank=True, default=list)),
                ("credit_required", models.BooleanField(default=True)),
                ("credit_text", models.CharField(blank=True, default="", max_length=500)),
                ("evidence_url", models.URLField(blank=True, default="", max_length=2000)),
                ("evidence_note", models.TextField(blank=True, default="")),
                ("consent_version", models.CharField(blank=True, default="", max_length=50)),
                ("granted_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="recorded_ugc_rights_passports",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "submission",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rights_passport",
                        to="common.ugcsubmission",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ugc_rights_passports",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={"db_table": "common_ugc_rights_passport", "ordering": ["-updated_at"]},
        ),
        migrations.AddIndex(
            model_name="ugccreator",
            index=models.Index(
                fields=["workspace", "relationship_stage", "-last_seen_at"], name="ugc_creator_stage_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="ugccreator",
            index=models.Index(fields=["workspace", "-last_seen_at"], name="ugc_creator_seen_idx"),
        ),
        migrations.AddConstraint(
            model_name="ugccreatoridentity",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("normalized_handle", "")),
                fields=("workspace", "platform", "normalized_handle"),
                name="ugc_identity_handle_uniq",
            ),
        ),
        migrations.AddConstraint(
            model_name="ugccreatoridentity",
            constraint=models.UniqueConstraint(
                condition=~models.Q(("external_id", "")),
                fields=("workspace", "platform", "external_id"),
                name="ugc_identity_external_uniq",
            ),
        ),
        migrations.AddIndex(
            model_name="ugccreatoridentity",
            index=models.Index(fields=["workspace", "platform", "normalized_handle"], name="ugc_identity_lookup_idx"),
        ),
        migrations.AddIndex(
            model_name="ugcrightspassport",
            index=models.Index(fields=["workspace", "status", "-updated_at"], name="ugc_rights_status_idx"),
        ),
        migrations.AddIndex(
            model_name="ugcrightspassport",
            index=models.Index(fields=["workspace", "expires_at"], name="ugc_rights_expiry_idx"),
        ),
        migrations.RunPython(backfill_creator_relationships_and_rights, noop_reverse),
    ]
