import django.db.models.deletion
import seo.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('media_library', '0001_initial'),
        ('pages', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='page',
            name='canonical_url',
            field=models.URLField(blank=True, default='', help_text='別URLを正規とする場合だけ指定します。', validators=[seo.validators.validate_canonical_url], verbose_name='正規URL'),
        ),
        migrations.AddField(
            model_name='page',
            name='noindex',
            field=models.BooleanField(default=False, verbose_name='検索エンジンから除外'),
        ),
        migrations.AddField(
            model_name='page',
            name='og_image',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='og_pages', to='media_library.mediaasset', verbose_name='OG画像'),
        ),
        migrations.AddField(
            model_name='page',
            name='seo_description',
            field=models.CharField(blank=True, default='', help_text='空なら本文の冒頭を使います。', max_length=160, verbose_name='SEO説明文'),
        ),
        migrations.AddField(
            model_name='page',
            name='seo_title',
            field=models.CharField(blank=True, default='', help_text='空なら固定ページのタイトルを使います。', max_length=70, verbose_name='SEOタイトル'),
        ),
    ]
