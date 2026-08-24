from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("workspaces", "0004_workspace_discovery_searches"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="community_smart_rules",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
