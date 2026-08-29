import seo.themes
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('seo', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesetting',
            name='enable_motion',
            field=models.BooleanField(default=False, help_text='利用者の「動きを減らす」設定は常に優先されます。', verbose_name='アニメーションを有効にする'),
        ),
        migrations.AddField(
            model_name='sitesetting',
            name='theme_key',
            field=models.CharField(default='clean', max_length=40, validators=[seo.themes.validate_theme_key], verbose_name='テーマ'),
        ),
    ]
