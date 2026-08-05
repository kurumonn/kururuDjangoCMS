# 4日目に実際に起きたエラー

## 1. テンプレートを編集したのに画面が変わらない

**症状**

`article_detail.html` にコメント欄と関連記事を追加したが、
ブラウザーを再読み込みしても古い画面のまま。エラーは何も出ない。

**再現条件**

`runserver` を `--noreload` 付きで起動したまま、テンプレートだけを編集した。

**原因**

Django 4.1 以降、`DEBUG=True` でもテンプレートは
**プロセス内でキャッシュ**されます（`cached.Loader` 相当の挙動）。
通常は自動リロードでプロセスごと再起動するため気づきませんが、
`--noreload` を付けているとプロセスが生き続けるので、
古いテンプレートが返り続けます。

Python ファイルを直しても反映されないのも同じ理由です。

**やってしまいがちな遠回り**

* ブラウザーのキャッシュを疑ってスーパーリロードする → 変わらない
* `collectstatic` を実行する → テンプレートは静的ファイルではないので無関係
* テンプレートの継承やブロック名を疑って書き換える → 元から正しい

**直し方**

サーバーを再起動します。

```bash
python manage.py runserver
```

`--noreload` を外せば自動リロードされます。
自動リロードを切りたい事情がある場合（プロセス数を固定したいなど）は、
編集のたびに再起動する運用にします。

**判断方法**

再起動後にページを開き、追加した見出し（「コメント」など）が表示されること。

---

## 2. `X-Forwarded-For` からどの値を取るべきか間違える

**症状**

例外は出ないが、レート制限が効かない、あるいは全員が同じ IP として扱われる。

**再現条件**

Nginx の背後に置いた Django で、`X-Forwarded-For` の **左端** を利用者の IP として採用する。

**原因**

Nginx の次の設定は、受け取ったヘッダーの **末尾へ** 接続元を追記します。

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
```

つまり、利用者が最初から偽のヘッダーを付けて送ってくると、こうなります。

```text
利用者が送ったヘッダー : X-Forwarded-For: 1.2.3.4
Nginx が書き換えた結果 : X-Forwarded-For: 1.2.3.4, 203.0.113.9
                                                    ↑ここだけが信用できる
```

左端（`1.2.3.4`）を採用する実装にすると、利用者は好きな IP を名乗れます。
レート制限も IP 制限も、リクエストごとに違う値を送るだけで回避されます。

**直し方**

信頼するプロキシの段数を設定で明示し、右から数えます。

```python
proxy_count = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
if proxy_count > 0:
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    index = len(parts) - proxy_count
    if 0 <= index < len(parts):
        return parts[index]
return request.META.get("REMOTE_ADDR", "")
```

* プロキシ無し（開発）: `TRUSTED_PROXY_COUNT = 0` → ヘッダーを一切見ない
* Nginx 1段: `1` → 右端
* CDN + Nginx: `2` → 右から2番目

**判断方法**

テストで確認します。

```python
request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9")
with override_settings(TRUSTED_PROXY_COUNT=1):
    assert client_ip(request) == "203.0.113.9"
```

**補足: このとき私が間違えたのはテストの方でした**

実装は最初から正しく右から数えていましたが、
テストの期待値を「左端が正しい」と書いてしまい、失敗しました。

```text
AssertionError: '10.0.0.1' != '203.0.113.9'
```

実装を疑って直そうとしたのですが、Nginx の追記方向を確認した結果、
**テストの期待値が間違っている**と分かりました。
テストが落ちたとき、必ずしも実装が悪いとは限りません。

---

## 3. アップロード検証で Pillow がファイルを読めない

**症状**

正しい画像をアップロードしたのに「画像として読み取れませんでした」になる。

**原因**

`verify()` を呼んだ後の Pillow の画像オブジェクトは、再利用できません。
また、アップロードファイルは一度読むとファイルポインタが末尾へ進むため、
そのまま2回目を読むと空になります。

**直し方**

内容をいったんメモリへ読み、`BytesIO` から2回開き直します。
検証が終わったら、保存処理のためにポインタを先頭へ戻します。

```python
payload = _read_all(uploaded_file)

with Image.open(io.BytesIO(payload)) as image:
    image.verify()           # 壊れていないか
with Image.open(io.BytesIO(payload)) as image:
    image_format = image.format   # 開き直して形式を採る
    width, height = image.size

uploaded_file.seek(0)        # 保存処理のために巻き戻す
```

**判断方法**

検証後に読み直せることをテストで固定します。

```python
uploaded = SimpleUploadedFile("photo.png", png_bytes)
validate_image_upload(uploaded)
assert uploaded.tell() == 0
assert uploaded.read()      # 中身がある
```

---

## レビューで見つかった3件（公開前に修正）

以下は自分では気づけず、記事のレビューで指摘されて直したものです。
3件とも「動いていた」ので、テストでは拾えていませんでした。

### 1. 「SVG から セッション Cookie も読める」は誤り

**書いていたこと**

> セッション Cookie も読めます。

**なぜ誤りか**

1日目で `SESSION_COOKIE_HTTPONLY = True` を設定しています。
`HttpOnly` が付いた Cookie は JavaScript から読めません。
`document.cookie` にセッション ID は現れません。

**では安全なのか**

いいえ。ここを取り違えると逆に危険です。
**Cookie を読めなくても、Cookie は自動的に送られます。**

同一オリジンでスクリプトが動けば、ログイン状態のまま
記事の投稿・編集・削除ができ、CSRF トークンも画面から読めます。
DOM に出ている情報も外部へ送れます。

「盗まれる」ではなく「被害者のブラウザーが操作台として使われる」。

**教訓**

自分の設定を確認せずに、一般論としての XSS の説明を書いていました。
`HttpOnly` を設定した回（1日目）を自分で書いているのに、
3日後の記事でその前提を無視しています。

### 2. IP ハッシュを「SECRET_KEY を知らなければ復元できない」と断定していた

**書いていたこと**

```python
salted = f"{settings.SECRET_KEY}:{ip}".encode("utf-8")
return hashlib.sha256(salted).hexdigest()
```

> 元の IP は簡単には戻せない（`SECRET_KEY` を知らないと総当たりできない）

**何が不正確か**

言えるのは「鍵なしの SHA-256 より総当たりが難しくなる」までです。
IPv4 は約43億通りしかないので、**鍵が漏れれば数分で全部試せます**。
これは匿名化ではなく、DB 単体が漏れた場合の緩和策です。

**実装も2点直した**

1. 連結ではなく **HMAC** を使う。
   `SHA-256(鍵 ‖ データ)` は長さ拡張攻撃が成立する形として知られており、
   鍵付きハッシュには HMAC を使うのが標準の構成です。

2. `SECRET_KEY` ではなく **専用の鍵**（`COMMENT_IP_HASH_KEY`）を使う。
   鍵の寿命が違うためです。`SECRET_KEY` は漏えいしたら必ず入れ替えますが、
   同じ鍵で IP をハッシュしていると、入れ替えた瞬間に過去のハッシュと
   一致しなくなり、連投検出の履歴が切れます。
   「鍵を入れ替えなければならない日」に「入れ替えると別の機能が壊れる」
   状態を作らないこと。

### 3. `Image.MAX_IMAGE_PIXELS` をリクエストごとに書き換えていた

これが3件のうち最も実害がありました。**2つの不具合が重なっていました。**

**書いていたこと**

```python
original_limit = Image.MAX_IMAGE_PIXELS
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
try:
    ...
finally:
    Image.MAX_IMAGE_PIXELS = original_limit
```

**不具合A: 他のリクエストに影響する**

`Image.MAX_IMAGE_PIXELS` は Pillow モジュール全体で共有される変数です。
同じプロセスで複数リクエストを同時に処理する構成（Gunicorn の
スレッドワーカーなど）では、片方の書き換えがもう片方から見えます。

`finally` で戻しているので前後を比べれば差はありません。
**問題が起きるのはその途中**です。
そのため、前後を比較するテストでは検出できません。

**不具合B: 上限がそもそも効いていなかった**

Pillow はこの値を境に2段階の反応をします。

| 画素数 | 反応 |
| --- | --- |
| 上限以下 | 何もしない |
| 上限超 | `DecompressionBombWarning`（警告だけ） |
| 上限の2倍超 | `DecompressionBombError`（例外） |

実測（Pillow 12.3.0、`MAX_IMAGE_PIXELS = 50_000_000`）:

```text
10000x4900  =  49,000,000 px -> 通った / 警告なし
10000x6000  =  60,000,000 px -> 通った / DecompressionBombWarning
10000x12000 = 120,000,000 px -> DecompressionBombError
```

**「上限 50 メガピクセル」と書いておきながら、1億画素まで通していました。**

**直し方**

Pillow の共有設定には触れず、開いた後に自分で数えます。

```python
with Image.open(io.BytesIO(payload)) as image:
    width, height = image.size

if width * height > MAX_IMAGE_PIXELS:
    raise UploadValidationError("画像の画素数が大きすぎます。")
```

`Image.open()` はヘッダーしか読まないので、伸長する前に数えられます。

**判断方法**

不具合Aは前後比較では見えないので、**検証の最中**を観測します。

```python
def spy(*args, **kwargs):
    observed.append(Image.MAX_IMAGE_PIXELS)
    return original_open(*args, **kwargs)

with mock.patch.object(Image, "open", spy):
    validate_image_upload(...)

for seen in observed:
    self.assertEqual(seen, Image.MAX_IMAGE_PIXELS)
```

不具合Bは、上限と2倍の**間**の画像で確かめます。

追加したテストは、修正前の実装に対して落ちることを確認してから直しました。

```text
FAIL: test_global_pillow_limit_is_untouched_during_validation
AssertionError: 50000000 != 89478485
FAIL: test_oversized_image_is_rejected_by_our_own_check
AssertionError: UploadValidationError not raised
```

2つ目の `not raised` が、不具合Bそのものです。

### 3件に共通すること

どれも**テストが通っていて、動いていて、それでも間違っていました**。

- 1件目は、自分が3日前に書いた設定を無視した説明
- 2件目は、効果の範囲を広く言いすぎた説明
- 3件目は、上限が効いていると思い込んで境界を試していなかった

境界値を試す、共有状態は途中を見る、
「〜できない」と書く前に自分の設定を確認する。
