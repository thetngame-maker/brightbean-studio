# Generated manually for TN Social Studio UGC foundation.

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("common", "0001_audit_event"),
        ("media_library", "0003_pendingupload"),
        ("workspaces", "0003_alter_workspace_primary_color_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UGCSubmission",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("photo", "Photo"), ("review", "Review"), ("tip", "Tip"), ("trail_report", "Trail report"), ("community_post", "Community post")], db_index=True, max_length=30)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("removed", "Removed")], db_index=True, default="pending", max_length=20)),
                ("source", models.CharField(choices=[("ui", "User interface"), ("api", "API"), ("webhook", "Webhook"), ("import", "Import")], default="ui", max_length=20)),
                ("contributor_external_id", models.CharField(blank=True, db_index=True, default="", max_length=255)),
                ("contributor_name", models.CharField(blank=True, default="", max_length=255)),
                ("contributor_handle", models.CharField(blank=True, default="", max_length=255)),
                ("attribution", models.CharField(choices=[("name", "Display name"), ("handle", "Handle"), ("anonymous", "Anonymous")], default="name", max_length=20)),
                ("target_type", models.CharField(db_index=True, max_length=100)),
                ("target_id", models.CharField(db_index=True, max_length=255)),
                ("target_label", models.CharField(blank=True, default="", max_length=255)),
                ("target_url", models.URLField(blank=True, default="", max_length=2000)),
                ("title", models.CharField(blank=True, default="", max_length=255)),
                ("body", models.TextField(blank=True, default="")),
                ("rating", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("consent_confirmed", models.BooleanField(default=False)),
                ("consent_version", models.CharField(blank=True, default="", max_length=50)),
                ("consent_at", models.DateTimeField(blank=True, null=True)),
                ("moderated_at", models.DateTimeField(blank=True, null=True)),
                ("moderation_note", models.TextField(blank=True, default="")),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("submitted_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("contributor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ugc_submissions", to=settings.AUTH_USER_MODEL)),
                ("media_asset", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ugc_submissions", to="media_library.mediaasset")),
                ("moderated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="moderated_ugc_submissions", to=settings.AUTH_USER_MODEL)),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ugc_submissions", to="workspaces.workspace")),
            ],
            options={"db_table": "common_ugc_submission", "ordering": ["-submitted_at"]},
        ),
        migrations.CreateModel(
            name="UGCModerationEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(choices=[("approve", "Approve"), ("reject", "Reject"), ("remove", "Remove"), ("restore", "Restore"), ("note", "Note")], max_length=20)),
                ("from_status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("removed", "Removed")], max_length=20)),
                ("to_status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("removed", "Removed")], max_length=20)),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ugc_moderation_events", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="moderation_events", to="common.ugcsubmission")),
            ],
            options={"db_table": "common_ugc_moderation_event", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="UGCReport",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("reporter_external_id", models.CharField(blank=True, default="", max_length=255)),
                ("reporter_name", models.CharField(blank=True, default="", max_length=255)),
                ("reason", models.CharField(choices=[("spam", "Spam"), ("inappropriate", "Inappropriate content"), ("incorrect", "Incorrect information"), ("copyright", "Copyright"), ("privacy", "Privacy"), ("other", "Other")], db_index=True, max_length=30)),
                ("details", models.TextField(blank=True, default="")),
                ("status", models.CharField(choices=[("open", "Open"), ("reviewing", "Reviewing"), ("resolved", "Resolved"), ("dismissed", "Dismissed")], db_index=True, default="open", max_length=20)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                ("resolution_note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("handled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="handled_ugc_reports", to=settings.AUTH_USER_MODEL)),
                ("reporter", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="ugc_reports", to=settings.AUTH_USER_MODEL)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="common.ugcsubmission")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="ugc_reports", to="workspaces.workspace")),
            ],
            options={"db_table": "common_ugc_report", "ordering": ["-created_at"]},
        ),
        migrations.AddIndex(model_name="ugcsubmission", index=models.Index(fields=["workspace", "status", "-submitted_at"], name="ugc_ws_status_idx")),
        migrations.AddIndex(model_name="ugcsubmission", index=models.Index(fields=["workspace", "kind", "-submitted_at"], name="ugc_ws_kind_idx")),
        migrations.AddIndex(model_name="ugcsubmission", index=models.Index(fields=["workspace", "target_type", "target_id"], name="ugc_ws_target_idx")),
        migrations.AddConstraint(model_name="ugcsubmission", constraint=models.CheckConstraint(condition=models.Q(("rating__isnull", True), models.Q(("rating__gte", 1), ("rating__lte", 5)), _connector="OR"), name="ugc_rating_1_to_5")),
        migrations.AddIndex(model_name="ugcmoderationevent", index=models.Index(fields=["submission", "-created_at"], name="ugc_mod_submission_idx")),
        migrations.AddIndex(model_name="ugcreport", index=models.Index(fields=["workspace", "status", "-created_at"], name="ugc_report_ws_status_idx")),
        migrations.AddIndex(model_name="ugcreport", index=models.Index(fields=["submission", "status"], name="ugc_report_submission_idx")),
    ]
