import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("composer", "0020_platformpost_first_comment_state"),
        ("workspaces", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FacebookGroupTarget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("url", models.URLField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workspace",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="facebook_group_targets",
                        to="workspaces.workspace",
                    ),
                ),
            ],
            options={
                "db_table": "composer_facebook_group_target",
                "ordering": ["name", "created_at"],
            },
        ),
        migrations.CreateModel(
            name="PostFacebookGroupTarget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[("pending", "Pending"), ("posted", "Posted"), ("skipped", "Skipped")],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("posted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="facebook_group_targets",
                        to="composer.post",
                    ),
                ),
                (
                    "target",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="post_targets",
                        to="composer.facebookgrouptarget",
                    ),
                ),
            ],
            options={
                "db_table": "composer_post_facebook_group_target",
                "ordering": ["created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="facebookgrouptarget",
            constraint=models.UniqueConstraint(
                fields=("workspace", "url"),
                name="composer_fb_group_workspace_url_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="postfacebookgrouptarget",
            constraint=models.UniqueConstraint(
                fields=("post", "target"),
                name="composer_post_fb_group_unique",
            ),
        ),
    ]
