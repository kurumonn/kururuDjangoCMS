from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True
    dependencies = []
    operations = [
        migrations.CreateModel(
            name="PluginActivation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.SlugField(max_length=80, unique=True, verbose_name="プラグインキー")),
                ("enabled", models.BooleanField(default=False, verbose_name="有効")),
                ("config", models.JSONField(blank=True, default=dict, verbose_name="設定")),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "プラグイン有効化", "verbose_name_plural": "プラグイン有効化"},
        )
    ]
