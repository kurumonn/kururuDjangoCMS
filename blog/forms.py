"""記事の入力フォーム。

View は「入力を受け取ってレスポンスを返す」役割、
Form は「入力が正しいかを検証する」役割に分ける。
検証を View へ書くと、投稿・編集・API で同じチェックを3回書くことになる。
"""

from django import forms
from django.utils import timezone

from .blocks import validate_blocks
from .models import Article


class ArticleForm(forms.ModelForm):
    """記事の投稿・編集フォーム。

    author はフォームに含めない。画面から送られてきた値で著者を決めると、
    他人の名前で記事を投稿できてしまうため、View 側で request.user を入れる。

    公開状態の選択肢は、ログイン中のユーザーの権限で絞る。
    画面から「公開」を消すだけでは足りない（POST を直接送れば通ってしまう）。
    このフォームは choices を実際に差し替えるため、
    権限のない値を送っても検証で弾かれる。
    """

    version = forms.IntegerField(required=False, widget=forms.HiddenInput)

    class Meta:
        model = Article
        fields = [
            "title",
            "body",
            "blocks",
            "category",
            "tags",
            "featured_image",
            "status",
            "published_at",
            # SEO（6日目に追加）
            "seo_title",
            "seo_description",
            "canonical_url",
            "og_image",
            "noindex",
        ]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 18}),
            # ブロックはエディターが JSON を書き込むので、素の欄は隠す。
            # JavaScript が無効な環境でも JSON を直接編集できるよう、
            # 消すのではなく hidden にしておく。
            "blocks": forms.HiddenInput(),
            "published_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "tags": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        if self.instance.pk:
            self.fields["version"].initial = self.instance.version

        # datetime-local 入力は "YYYY-MM-DDTHH:MM" 形式しか受け付けない。
        self.fields["published_at"].input_formats = [
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M:%S",
        ]
        self.fields["tags"].required = False
        # ブロックは空（ブロックを使わない記事）でもよい。
        self.fields["blocks"].required = False
        self.fields["body"].required = False

        self.fields["status"].choices = self._allowed_status_choices()
        if not self.can_publish:
            self.fields["published_at"].disabled = True
            self.fields["published_at"].help_text = (
                "公開日時を設定できるのは、公開権限を持つ利用者だけです。"
            )

    @property
    def can_publish(self) -> bool:
        return bool(self.user and self.user.has_perm("blog.publish_article"))

    def _allowed_status_choices(self):
        """権限に応じて選べる公開状態を決める。"""
        if self.can_publish:
            return Article.Status.choices
        # 公開権限が無い人は「下書き」と「レビュー待ち」まで。
        return [
            (value, label)
            for value, label in Article.Status.choices
            if value != Article.Status.PUBLISHED
        ]

    def clean_status(self):
        status = self.cleaned_data.get("status")
        if status == Article.Status.PUBLISHED and not self.can_publish:
            raise forms.ValidationError("記事を公開する権限がありません。")
        return status

    def clean_blocks(self):
        return validate_blocks(self.cleaned_data.get("blocks"))

    def clean_version(self):
        value = self.cleaned_data.get("version")
        if self.instance.pk and value is None:
            raise forms.ValidationError("編集対象の版番号がありません。再読み込みしてください。")
        return value

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("status")
        published_at = cleaned.get("published_at")

        # 「公開」にしたのに公開日時が無い場合は、現在時刻を補う。
        # 空のままだと published() の条件に一致せず、
        # 「公開したはずなのに一覧に出ない」という分かりにくい状態になる。
        if status == Article.Status.PUBLISHED and not published_at:
            cleaned["published_at"] = timezone.now()

        # 本文がまったく無い記事は保存させない。
        # body とブロックの両方が空だと、タイトルだけの記事が公開できてしまう。
        if not cleaned.get("body") and not cleaned.get("blocks"):
            self.add_error("body", "本文かブロックのどちらかを入力してください。")

        return cleaned


class RevisionNoteForm(forms.Form):
    """レビュー依頼・承認・差し戻しに添えるメモ。"""

    note = forms.CharField(
        label="メモ",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"placeholder": "任意（差し戻し理由など）"}),
    )
