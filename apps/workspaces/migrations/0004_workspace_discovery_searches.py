from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workspaces", "0003_alter_workspace_primary_color_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="workspace",
            name="discovery_searches",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
