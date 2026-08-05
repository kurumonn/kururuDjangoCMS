# 【4日目】Django CMS を実用化――画像投稿・コメント・サイト内検索を作る

> 連載「10日で作る Django CMS」の4日目です。
> 成果物: <https://github.com/kurumonn/DjangoCMS>（タグ `day-04`）

---

## 1. 今日の結論

CMS として使えるようにする機能を足します。

- メディアライブラリ（アップロードした画像を1か所で管理する）
- **アップロードファイルの検証**（今日の主役）
- コメント投稿と承認制
- サイト内検索
- 関連記事

**今日いちばん大事なのは、「拡張子が `.jpg` だから画像である」という判断が
成立しないことを理解すること**です。ファイル名は攻撃者が自由に決められます。

---

## 2. 今日の完成画面

記事詳細に、関連記事とコメント欄が付きます。

<!-- screenshot: day-04-article-detail.png | 記事詳細とコメント欄 -->

サイト内検索も動きます。

<!-- screenshot: day-04-search.png | サイト内検索 -->

---

## 3. 今日変更するファイル

```text
media_library/            新規アプリ
├── models.py             MediaAsset
├── validators.py         アップロード検証（今日の主役）
├── admin.py
└── tests.py
comments/                 新規アプリ
├── models.py             Comment
├── forms.py              ハニーポット付きフォーム
├── views.py
├── urls.py
├── admin.py
└── tests.py
core/                     新規（アプリ横断の道具）
└── ratelimit.py          簡易レート制限
blog/
├── models.py             変更（featured_image / search()）
├── views.py              変更（SearchView / コメント表示）
└── forms.py              変更
templates/
├── blog/article_detail.html   変更
└── blog/search.html           新規
config/settings.py        変更（アップロード上限など）
```

---

## 4. 完成コード

### 4.1 アップロード検証

これが今日の中心です。

```python
# media_library/validators.py
"""アップロードされた画像の検証。

アップロード処理は CMS でもっとも攻撃されやすい入口のひとつ。
「拡張子が .jpg だから画像だ」という判断は成立しない。
攻撃者はファイル名を自由に決められるため、次の3点を必ず確認する。

  1. サイズ  … 巨大ファイルでディスクとメモリを枯渇させられる
  2. 実体    … 中身が本当に画像か（Pillow に開かせて確認する）
  3. 形式    … 開けたとしても、許可した形式かどうか

特に SVG は「画像」でありながら中に JavaScript を書ける。
同一オリジンで配信すると XSS になるため、この CMS では受け付けない。
"""

import io

from django.core.exceptions import ValidationError

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024        # 5 MiB
MAX_IMAGE_PIXELS = 50_000_000            # 50 メガピクセル


def validate_image_upload(uploaded_file, *, max_size: int = MAX_UPLOAD_SIZE) -> str:
    """アップロードファイルを検証し、Pillow が判定した形式名を返す。"""

    # --- 1. サイズ -------------------------------------------------------
    size = getattr(uploaded_file, "size", None)
    if size is None:
        raise UploadValidationError("ファイルサイズを取得できませんでした。")
    if size == 0:
        raise UploadValidationError("空のファイルはアップロードできません。")
    if size > max_size:
        limit_mb = max_size / (1024 * 1024)
        raise UploadValidationError(f"ファイルサイズが大きすぎます（上限 {limit_mb:.0f} MB）。")

    # --- 2. 拡張子 -------------------------------------------------------
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if "." not in name:
        raise UploadValidationError("拡張子のないファイルは受け付けません。")
    extension = name[name.rfind(".") :]
    if extension not in ALLOWED_EXTENSIONS:
        allowed = "、".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadValidationError(f"この拡張子は許可されていません（許可: {allowed}）。")

    # --- 3. 実体 ---------------------------------------------------------
    from PIL import Image, UnidentifiedImageError

    # ★Image.MAX_IMAGE_PIXELS を書き換えないこと（理由は6章）★
    try:
        payload = _read_all(uploaded_file)

        # SVG は Pillow が開けないので拡張子で弾かれるが、
        # 「画像に見えるテキスト」を明示的に拒否したことを記録として残す。
        if payload.lstrip()[:5].lower() in (b"<?xml", b"<svg"):
            raise UploadValidationError(
                "SVG は受け付けません（内部にスクリプトを埋め込めるため）。"
            )

        try:
            # まず開いて、寸法と形式だけを取る。
            # Image.open() はヘッダーしか読まないので、
            # この時点では画素データを伸長していない。
            with Image.open(io.BytesIO(payload)) as image:
                image_format = image.format
                width, height = image.size

            # 伸長する前に、自分で画素数を数えて判定する。
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadValidationError(
                    f"画像の画素数が大きすぎます"
                    f"（{width}×{height}、上限 {MAX_IMAGE_PIXELS:,} 画素）。"
                )

            # 壊れたファイルの検出。verify() の後は読み直しが要る。
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
        except UnidentifiedImageError as exc:
            raise UploadValidationError(
                "画像として読み取れませんでした。"
                "拡張子だけを変えたファイルの可能性があります。"
            ) from exc
        except Image.DecompressionBombError as exc:
            # Pillow 自身の既定値に引っかかった場合の受け皿。
            raise UploadValidationError("画像の画素数が大きすぎます。") from exc
        except OSError as exc:
            raise UploadValidationError("壊れた画像ファイルです。") from exc
    finally:
        # 後続の保存処理のために先頭へ巻き戻す。
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)

    if image_format not in ALLOWED_IMAGE_FORMATS:
        allowed = "、".join(sorted(ALLOWED_IMAGE_FORMATS))
        raise UploadValidationError(f"この画像形式は許可されていません（許可: {allowed}）。")

    # 拡張子と実体が食い違うファイルを拒否する。
    expected = {
        ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
        ".gif": "GIF", ".webp": "WEBP",
    }[extension]
    if expected != image_format:
        raise UploadValidationError(
            f"拡張子（{extension}）と実際の形式（{image_format}）が一致しません。"
        )

    return image_format
```

### 4.2 保存先のパス

```python
# media_library/models.py（抜粋）
import secrets


def upload_to(instance, filename: str) -> str:
    """保存先のパスを決める。

    利用者が付けたファイル名は使わない。理由は3つある。

      1. ../../etc/passwd のようなパス移動を狙う名前を防ぐ
      2. 日本語や記号を含む名前が環境によって壊れるのを防ぐ
      3. secret-contract.pdf のような名前から中身を推測されるのを防ぐ

    拡張子だけは検証済みのものを引き継ぐ。
    """
    extension = ""
    if "." in filename:
        extension = filename[filename.rfind(".") :].lower()
    stamp = instance.created_at if instance.created_at else None
    prefix = stamp.strftime("%Y/%m") if stamp else "unsorted"
    return f"library/{prefix}/{secrets.token_hex(16)}{extension}"
```

### 4.3 コメントモデル

```python
# comments/models.py（抜粋）
import hashlib
import hmac

from django.conf import settings
from django.db import models


def _ip_hash_key() -> bytes:
    """IP ハッシュ専用の鍵を返す。

    SECRET_KEY をそのまま使わないのは、用途が違うため。
    SECRET_KEY は漏えい時に必ず入れ替えるが、そのとき
    IP ハッシュまで一斉に変わると、連投の検出やスパム対策の
    履歴が過去と繋がらなくなる。鍵の寿命が違うものは鍵を分ける。
    """
    key = getattr(settings, "COMMENT_IP_HASH_KEY", "") or settings.SECRET_KEY
    return key.encode("utf-8")


def hash_ip(ip: str | None) -> str:
    """IP アドレスを鍵付きハッシュ（HMAC-SHA256）に変換する。

    これは匿名化ではなく、DB 単体が漏れた場合の緩和策。

      * 鍵なしの SHA-256 だけなら、IPv4 は約43億通りしかないので
        総当たりで元の IP を求められる。実質的に可逆。
      * 鍵付き HMAC なら、鍵を持たない相手は総当たりできない。
      * ただし鍵も一緒に漏れれば、同じく総当たりできる。
    """
    if not ip:
        return ""
    return hmac.new(_ip_hash_key(), ip.encode("utf-8"), hashlib.sha256).hexdigest()


class Comment(models.Model):
    article = models.ForeignKey(
        "blog.Article", on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="comments",
    )
    name = models.CharField("表示名", max_length=50)
    email = models.EmailField("メールアドレス", blank=True, default="")
    body = models.TextField("本文", max_length=2000)

    is_approved = models.BooleanField("承認済み", default=False)
    is_spam = models.BooleanField("スパム", default=False)

    ip_hash = models.CharField("IPハッシュ", max_length=64, blank=True, editable=False)
    created_at = models.DateTimeField("投稿日時", auto_now_add=True)
```

### 4.4 スパム対策（CAPTCHA なし）

```python
# comments/forms.py（抜粋）
MIN_FILL_SECONDS = 3


class CommentForm(forms.ModelForm):
    """CAPTCHA を使わずにスパムを減らす手段を2つ入れる。

      1. ハニーポット … 人間には見えない入力欄。自動入力ボットだけが埋める。
      2. 送信までの時間 … 表示から3秒未満の送信は機械とみなす。

    どちらも完璧ではないが、利用者に負担をかけずに
    大半の自動投稿を落とせる。
    """

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            "class": "honeypot", "tabindex": "-1",
            "autocomplete": "off", "aria-hidden": "true",
        }),
        label="ウェブサイト（入力しないでください）",
    )
    rendered_at = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def clean_website(self):
        if self.cleaned_data.get("website", ""):
            raise forms.ValidationError("送信を受け付けられませんでした。")
        return ""

    def clean_rendered_at(self):
        value = self.cleaned_data.get("rendered_at")
        if value is None:
            raise forms.ValidationError("送信を受け付けられませんでした。")

        elapsed = int(time.time()) - int(value)
        if elapsed < MIN_FILL_SECONDS:
            raise forms.ValidationError("送信が速すぎます。数秒おいてからお試しください。")
        return value
```

### 4.5 サイト内検索

```python
# blog/models.py（抜粋）
def search(self, query: str):
    """タイトルと本文からの全文検索。

    SQLite / PostgreSQL のどちらでも動くよう、まずは icontains で実装する。
    PostgreSQL へ移行したあとは SearchVector に差し替えられるよう、
    検索条件をこの1メソッドへ閉じ込めておく。
    """
    from django.db.models import Q

    query = (query or "").strip()
    if not query:
        return self.none()

    # 空白区切りの語をすべて含む記事を返す（AND 検索）。
    queryset = self
    for term in query.split()[:5]:  # 語数を制限し、極端に重いクエリを防ぐ
        queryset = queryset.filter(Q(title__icontains=term) | Q(body__icontains=term))
    return queryset
```

---

## 5. コードの意味

### `Image.verify()` を2回開き直す理由

```python
with Image.open(io.BytesIO(payload)) as image:
    image.verify()          # 壊れていないか調べる
with Image.open(io.BytesIO(payload)) as image:
    image_format = image.format   # 開き直して形式を採る
```

| 部分 | 意味 |
| --- | --- |
| `io.BytesIO(payload)` | メモリ上のバイト列をファイルのように扱う |
| `verify()` | 壊れた画像を検出する。実行後は画像を使えなくなる |
| `image.format` | Pillow が判定した形式（`"PNG"` など） |

`verify()` の後にそのまま `image.format` を読もうとすると失敗します。
これは Pillow の仕様です。

### `uploaded_file.seek(0)` を忘れない

```python
finally:
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
```

アップロードファイルは一度読むと、ファイルポインタが末尾へ進みます。
巻き戻さないと、保存処理が **0 バイトのファイル** を書き込みます。

テストで固定します。

```python
def test_file_pointer_is_rewound_for_saving(self):
    uploaded = upload("photo.png", make_image_bytes("PNG"))
    validate_image_upload(uploaded)
    self.assertEqual(uploaded.tell(), 0)
    self.assertTrue(uploaded.read())
```

### `Q` オブジェクト

```python
from django.db.models import Q

queryset.filter(Q(title__icontains=term) | Q(body__icontains=term))
```

| 記号 | 意味 |
| --- | --- |
| `\|` | OR |
| `&` | AND |
| `~` | NOT |
| `__icontains` | 大文字小文字を区別しない部分一致 |

`filter(a=1, b=2)` は AND になります。OR を書きたいときだけ `Q` が必要です。

### 鍵付きハッシュ（HMAC）で IP を保存する

```python
return hmac.new(_ip_hash_key(), ip.encode("utf-8"), hashlib.sha256).hexdigest()
```

IP アドレスをそのまま保存すると、データベースが漏れたときに
「誰がどの記事にコメントしたか」の追跡材料になります。

ハッシュにすると、**同じ人の連投は検出できます**（同じ IP は同じハッシュ）。

#### 「復元できない」とは書けない

ここは正確に書く必要があります。

**IPv4 は約43億通りしかありません。**
現代の PC なら、SHA-256 を43億回計算するのは数分の作業です。
つまり**鍵を混ぜないハッシュは、実質的に可逆**です。
パスワードと違って、候補が有限で、しかも少ないためです。

鍵を混ぜると、鍵を知らない相手はこの総当たりができません。
ここまでが効果です。

そして、ここから先は効果がありません。

- 鍵も一緒に漏れれば、同じく43億回試すだけで元の IP が分かります
- したがってこれは**匿名化ではなく、DB 単体が漏れた場合の緩和策**です
- 鍵は DB とは別の場所（環境変数）に置いて初めて意味を持ちます

「ハッシュ化してあるから個人情報ではない」とは言えません。

#### なぜ連結ではなく HMAC なのか

最初はこう書いていました。

```python
salted = f"{settings.SECRET_KEY}:{ip}".encode("utf-8")
return hashlib.sha256(salted).hexdigest()
```

動きますが、2点直しました。

**1. 連結ではなく HMAC を使う。**
文字列を繋ぐ方式は、鍵とデータの境界が曖昧になります。
`SHA-256(鍵 ‖ データ)` の形は、ハッシュ関数の内部構造によっては
**長さ拡張攻撃**が成立する形として知られています
（SHA-256 はこれに該当します）。
HMAC は、この問題を避けるために設計された標準の構成です。

今回の用途で直ちに危険になるわけではありませんが、
**鍵付きハッシュには HMAC を使う**と覚えておけば、
用途が変わったときに考え直さずに済みます。

**2. `SECRET_KEY` を使わず、専用の鍵を用意する。**

```python
COMMENT_IP_HASH_KEY = require("DJANGO_COMMENT_IP_HASH_KEY")
```

理由は**鍵の寿命が違う**ことです。

`SECRET_KEY` は、漏えいしたら必ず入れ替えます。
そのとき IP ハッシュも同じ鍵で作っていると、
入れ替えた瞬間に**過去のハッシュと一致しなくなります**。
連投の検出もスパム対策の履歴も、そこで切れます。

「鍵を入れ替えなければならない日」に、
「入れ替えると別の機能が壊れる」という状態を作らないでください。
これは鍵を分けるだけで避けられます。

---

## 6. 内部で起きていること

### 拡張子を変えたファイルはどう見えるか

```text
攻撃者が用意するファイル: shell.php
   ↓ ファイル名を変える
photo.png（中身は PHP のまま）
   ↓ アップロード
```

拡張子だけを見る検証は、これを通してしまいます。

Pillow に開かせると、こうなります。

```text
Image.open(b"<?php system($_GET['c']); ?>")
   ↓
UnidentifiedImageError
```

**中身を見れば分かります。**

### SVG がなぜ危険なのか

SVG は XML なので、中に JavaScript を書けます。

```xml
<svg xmlns="http://www.w3.org/2000/svg">
  <script>alert(document.cookie)</script>
</svg>
```

これを `/media/library/xxx.svg` として同一オリジンで配信し、
利用者がそのURLを直接開くと、**サイトのオリジンでスクリプトが動きます**。

ここで「セッション Cookie を盗まれる」と書きたくなりますが、
**それは正確ではありません。** 1日目でこう設定しています。

```python
SESSION_COOKIE_HTTPONLY = True
```

`HttpOnly` が付いた Cookie は JavaScript から読めません。
`document.cookie` にセッション ID は出てきません。

では安全かというと、そうではありません。
**Cookie を読めなくても、Cookie は自動的に送られます。**

同一オリジンでスクリプトが動けば、次のことができます。

| できること | 仕組み |
| --- | --- |
| ログイン状態のままリクエストを送る | `fetch()` に Cookie が自動で付く |
| CSRF トークンを取得する | 画面の HTML から読める |
| 記事の投稿・編集・削除 | 上の2つを組み合わせる |
| 画面に表示されている情報を送信する | DOM を読んで外部へ送る |
| メールアドレスの変更 | 設定画面を読み、その値で送信する |

つまり **Cookie の値そのものを持ち出す代わりに、
被害者のブラウザーを操作台として使う**という形になります。
「盗まれる」より「使われる」の方が実態に近いです。

`HttpOnly` は「Cookie の値が外部サーバーへ渡ること」を防ぎます。
これは意味のある防御です（渡ってしまえば攻撃者は
自分の環境から何度でも使えます）。
ただし**同一オリジンで JavaScript が動くこと自体は防げません**。

対策は次のいずれかです。

1. SVG を受け付けない（この CMS の選択）
2. 別ドメインから配信する
3. `Content-Disposition: attachment` で必ずダウンロードさせる

学習用の CMS では、いちばん単純な 1 を選びました。

### decompression bomb

```text
1000×1000 の PNG（数十 KB）
   ↓ 実は 50000×50000 と宣言されている
展開すると 25 億ピクセル → 数十 GB のメモリ
```

小さいファイルで、サーバーのメモリを食い尽くせます。

Pillow には上限の仕組みがあります。最初はこう書いていました。

```python
original_limit = Image.MAX_IMAGE_PIXELS
Image.MAX_IMAGE_PIXELS = 50_000_000
try:
    ...
finally:
    Image.MAX_IMAGE_PIXELS = original_limit
```

**この書き方は2つの理由で誤りでした。** 両方とも後から気づいたものです。

#### 誤り1: 他のリクエストに影響する

`Image.MAX_IMAGE_PIXELS` は、Pillow というモジュール全体で共有される
ただの変数です。プロセス内のどのコードから見ても同じ値です。

Gunicorn のスレッドワーカーのように、
**同じプロセスで複数のリクエストを同時に処理する構成**では、
片方の書き換えがもう片方から見えます。

```text
リクエストA: 50_000_000 に下げる
リクエストB: 画像を開く          ← A の設定が効いてしまう
リクエストA: 元の値に戻す
リクエストB: 画像を開く          ← 今度は既定値に戻っている
```

B の結果が A の進み具合で決まります。
**落ちるときと落ちないときがある**という、最も再現しにくい形です。

`finally` で戻しているので前後を比べれば差はありません。
問題が起きるのは**その途中**です。

#### 誤り2: そもそも 50 メガピクセルで止まっていなかった

こちらの方が深刻でした。

Pillow はこの値を境に**2段階**の反応をします。

| 画素数 | Pillow の反応 |
| --- | --- |
| `MAX_IMAGE_PIXELS` 以下 | 何もしない |
| `MAX_IMAGE_PIXELS` 超 | `DecompressionBombWarning`（**警告だけ**） |
| `MAX_IMAGE_PIXELS` の2倍超 | `DecompressionBombError`（例外） |

50 メガピクセルを指定しても、**例外になるのは 1億画素を超えてから**です。
その間の画像は警告が出るだけで、そのまま通っていました。

実際に確かめた結果です（Pillow 12.3.0、`MAX_IMAGE_PIXELS = 50_000_000`）。

```text
10000x4900  =  49,000,000 px -> 通った / 警告なし
10000x6000  =  60,000,000 px -> 通った / DecompressionBombWarning
10000x12000 = 120,000,000 px -> DecompressionBombError
```

真ん中の行が問題です。上限を超えているのに通っています。

「上限を 50 メガピクセルにした」と書いてあるのに、
実際には 1億画素まで通っていた、ということです。
テストは通っていました。境界の画像を試していなかったためです。

#### 直した形

Pillow の共有設定には触れず、開いた後に自分で数えます。

```python
with Image.open(io.BytesIO(payload)) as image:
    width, height = image.size

if width * height > MAX_IMAGE_PIXELS:
    raise UploadValidationError("画像の画素数が大きすぎます。")
```

`Image.open()` はヘッダーしか読まないので、この時点で
画素データは伸長されていません。**伸長する前に数えられます**。

共有されている値を書き換えないので、同時に何本走っていても
結果が変わりません。上限もそのままの値で効きます。

Pillow の既定値（約 89,478,485 画素）はそのまま残しておき、
自前の検査をすり抜けた場合の受け皿として `DecompressionBombError` も
捕まえています。

> どうしても Pillow の既定値そのものを変えたい場合は、
> リクエストごとではなく**起動時に1回だけ**設定します
> （`AppConfig.ready()` など）。
> 「一時的に変えて戻す」が成立しない種類の値だと考えてください。

### アップロード上限は3か所で決まる

```text
Nginx        client_max_body_size 10m;      ← デプロイ編6日目
   ↓
Django       DATA_UPLOAD_MAX_MEMORY_SIZE    ← 受け取る段階の保険
   ↓
アプリ        MAX_UPLOAD_SIZE                ← 実際の判定
```

Nginx の設定を忘れると、Django まで届く前に 413 で切られます。
逆に Nginx だけ緩くしても、Django 側で止まります。
**3つとも意識してそろえます。**

---

## 7. コマンドの説明

### `pip install pillow`

| 項目 | 内容 |
| --- | --- |
| 目的 | 画像を扱うライブラリを入れる |
| 正常例 | `Successfully installed pillow-12.3.0` |
| 異常例 | ビルドエラー（Linux では `libjpeg-dev` などが必要な場合がある） |
| 判断方法 | `python -c "from PIL import Image; print(Image.__version__)"` |

`ImageField` を使うと、Django のシステムチェックが Pillow を要求します。
入れずに `makemigrations` すると、次のエラーになります。

```text
fields.E210: Cannot use ImageField because Pillow is not installed.
```

### `python manage.py test media_library`

アップロード検証だけを試したいときに使います。

```text
Ran 20 tests in 0.4s
OK
```

---

## 8. よくあるエラー

記録は [`docs/errors/day-04.md`](../errors/day-04.md) にあります。

### 8.1 テンプレートを編集したのに画面が変わらない

**症状**: コメント欄を追加したのに、再読み込みしても古い画面のまま。エラーも出ない。

**原因**: Django 4.1 以降、`DEBUG=True` でもテンプレートは
**プロセス内でキャッシュ**されます。
`runserver --noreload` を使っているとプロセスが生き続けるため、
古いテンプレートが返り続けます。

**やってしまいがちな遠回り**:

- ブラウザーのキャッシュを疑ってスーパーリロードする → 変わらない
- `collectstatic` を実行する → テンプレートは静的ファイルではないので無関係
- テンプレートの継承やブロック名を疑って書き換える → 元から正しい

**対処**: サーバーを再起動します。`--noreload` を外せば自動リロードされます。

### 8.2 `X-Forwarded-For` からどの値を取るべきか間違える

**症状**: 例外は出ないが、レート制限が効かない。

**原因**: Nginx の `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` は、
受け取ったヘッダーの **末尾へ** 接続元を追記します。

```text
利用者が送ったヘッダー : X-Forwarded-For: 1.2.3.4        ← 偽装できる
Nginx が書き換えた結果 : X-Forwarded-For: 1.2.3.4, 203.0.113.9
                                                    ↑ここだけが信用できる
```

左端を採用すると、利用者が好きな IP を名乗れて、制限を無限に回避されます。

**対処**: 信頼するプロキシの段数を設定で明示し、右から数えます。

```python
proxy_count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
if proxy_count > 0:
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    index = len(parts) - proxy_count
    if 0 <= index < len(parts):
        return parts[index]
return request.META.get("REMOTE_ADDR", "")
```

**補足**: このとき間違えたのは **テストの方** でした。
実装は最初から正しく右から数えていたのに、
テストの期待値を「左端が正しい」と書いて失敗しました。
テストが落ちたとき、必ずしも実装が悪いとは限りません。

### 8.3 保存されたファイルが 0 バイトになる

**原因**: 検証でファイルを読み切ったあと、
`seek(0)` で巻き戻していません。「5. コードの意味」を参照してください。

### 8.4 コメントが表示されない

**仕様です。** この CMS では、コメントは既定で **未承認** です。
管理画面から承認すると表示されます。

```python
comment.is_approved = False   # 承認は管理者が行う
```

---

## 9. 動作確認

### 記事とコメント

- [ ] 記事詳細にコメント欄が出る
- [ ] コメントを投稿すると「承認後に表示されます」と案内される
- [ ] 投稿直後は、そのコメントが表示されない
- [ ] 管理画面で承認すると表示される
- [ ] コメント本文に `<script>alert(1)</script>` と書いても文字として表示される
- [ ] 下書き記事にはコメントできない

### アップロード検証

管理画面の「メディア」から、次を1つずつ試してください。

- [ ] 正しい PNG → 保存できる
- [ ] 5 MB を超えるファイル → 「大きすぎます」
- [ ] `.php` ファイル → 「この拡張子は許可されていません」
- [ ] SVG → 「SVG は受け付けません」
- [ ] テキストファイルを `.png` に改名 → 「画像として読み取れませんでした」
- [ ] 本物の PNG を `.jpg` に改名 → 「拡張子と実際の形式が一致しません」

最後の2つが通ってしまうなら、実体の検証が効いていません。

### 検索

- [ ] タイトルに含まれる語で検索できる
- [ ] 本文に含まれる語で検索できる
- [ ] 下書き記事は検索結果に出ない
- [ ] 空の検索語ではエラーにならない

---

## 10. セキュリティ上の注意

### 検証の順番に意味がある

```text
1. サイズ    … 先に切らないと、次の読み込みでメモリを食う
2. 拡張子    … 安いチェックで大半を落とす
3. 実体      … いちばん重い。ここまで来たものだけに行う
```

実体の検証はファイル全体をメモリへ読みます。
サイズを先に確認しないと、その時点で攻撃が成立します。

### `Content-Type` を信用しない

```python
def test_content_type_header_is_not_trusted(self):
    """Content-Type は利用者が自由に付けられるので、判断材料にしない。"""
    self.assert_rejected(upload("lie.png", b"not an image at all", "image/png"))
```

ブラウザーが送る `Content-Type` は、利用者が書き換えられます。
`curl -H "Content-Type: image/png"` と付ければ何でも名乗れます。

### ファイル名を保存先に使わない

```python
def test_uploaded_filename_is_not_used_as_is(self):
    path = upload_to(asset, "../../../etc/passwd.png")
    self.assertNotIn("..", path)
    self.assertNotIn("passwd", path)
```

Django の `FileSystemStorage` にもパス移動の対策はありますが、
**そもそも利用者の入力をパスに使わない** のが確実です。

### コメントは既定で非公開

```python
def save(self, commit=True):
    comment = super().save(commit=False)
    # 承認は管理者が行う。既定では公開しない。
    comment.is_approved = False
```

「公開してから消す」より「承認してから公開する」方が安全です。
スパムや誹謗中傷が一瞬でも表示される時間をゼロにできます。

### 検索語の長さと語数を制限する

```python
self.query = self.request.GET.get("q", "").strip()[:100]
```

```python
for term in query.split()[:5]:
```

制限が無いと、`?q=` に長大な文字列や大量の語を入れて、
サーバーに重い LIKE 検索を何十個も実行させられます。

---

## 11. 今日の復習問題

**問1.** 「拡張子が `.png` だから画像である」という判断が成立しないのはなぜですか。

**問2.** SVG を受け付けない理由を説明してください。
受け付ける場合、どのような対策が必要ですか。

**問3.** 検証の順番を「サイズ → 拡張子 → 実体」にしている理由は何ですか。

**問4.** コメントの IP アドレスを生のまま保存せず、
`SECRET_KEY` を混ぜたハッシュにするのはなぜですか。
`SECRET_KEY` を混ぜない単純な SHA-256 では不十分な理由も答えてください。

**問5.** `X-Forwarded-For` の左端の値を利用者の IP として採用すると、
どのような問題が起きますか。

<details>
<summary>解答</summary>

**問1.**
ファイル名は利用者が自由に決められるためです。
中身が PHP スクリプトでも、`.png` という名前を付けられます。
中身を Pillow に開かせて、実際に画像として読めるかを確認する必要があります。

**問2.**
SVG は XML なので、内部に `<script>` を書けます。
同一オリジンで配信すると、閲覧者のブラウザーでスクリプトが実行されます（XSS）。

`SESSION_COOKIE_HTTPONLY = True` にしてあるため、
セッション Cookie の値を JavaScript から読み出すことはできません。
しかし Cookie はリクエストに自動で付くので、
**ログイン状態のまま記事を投稿・削除したり、
画面に表示されている情報を外部へ送ったり**はできます。
CSRF トークンも画面から読めます。

受け付ける場合は、別ドメインから配信するか、
`Content-Disposition: attachment` で必ずダウンロードさせる必要があります。

**問3.**
実体の検証はファイル全体をメモリへ読み込みます。
サイズを先に確認しないと、その読み込み自体が攻撃になります。
拡張子の確認は安価なので、実体の検証より前に置いて大半を落とします。

**問4.**
生の IP を保存すると、データベースが漏れたときに
「誰がどの記事にコメントしたか」の追跡材料になります。
IPv4 は約43億通りしかないため、ソルト無しの SHA-256 は
全通りを計算して照合すれば元に戻せます。
`SECRET_KEY` を混ぜると、鍵を知らない限り総当たりができません。

**問5.**
Nginx はヘッダーの末尾へ接続元を追記するため、左端は利用者が偽装した値です。
左端を採用すると、リクエストごとに違う IP を名乗ることで
レート制限や IP 制限を完全に回避できます。

</details>

---

## 12. Git の差分

```text
タグ    : day-04
コミット: day-04: メディアライブラリ・コメント・サイト内検索を作る
```

```bash
git diff day-03 day-04
```

アップロード検証のテストだけを見たい場合はこちらです。

```bash
git show day-04 -- media_library/tests.py
```

---

## 13. 次回予告

5日目は、WordPress 級の記事管理を作ります。

- 下書き → レビュー待ち → 公開 の承認フロー
- **公開を独立した権限にする**（記事を書ける人＝勝手に公開できる人、にしない）
- 予約投稿
- リビジョン（変更履歴）と過去版への復元
- 追記専用の操作ログ

「公開」を独立した権限にするだけで、
アカウントを1つ乗っ取られたときの被害が大きく変わります。

次回 → [【5日目】WordPress 級の記事管理](day-05.md)
