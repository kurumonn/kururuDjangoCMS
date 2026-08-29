import seo.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0006_article_version'),
    ]

    operations = [
        migrations.AlterField(
            model_name='article',
            name='canonical_url',
            field=models.URLField(blank=True, default='', help_text='他サイトへ転載した記事など、正規のURLが別にある場合に指定する。', validators=[seo.validators.validate_canonical_url], verbose_name='正規URL'),
        ),
    ]
