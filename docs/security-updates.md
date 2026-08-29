# 依存パッケージのセキュリティ更新記録

脆弱性が公表されたときに、**このプロジェクトが実際に影響を受けるか**を
調べた結果と、その根拠を残します。

「上げておいた」だけでは、次に同じ脆弱性が話題になったときに
また一から調べ直すことになります。
どこを見て「到達しない」と判断したかまで書きます。

> 到達しないと判断したものも、**更新はします**。
> 到達可能性の評価は「今の構成では踏まない」という話であって、
> 「直さなくてよい」という話ではありません。
> 明日の変更で構成が変われば、評価はひっくり返ります。

---

## 2026-08-29 CMS認可・認証レート制限・運用防御の修正

対象コミット ab29a0651f82b72c01e1eed42eb4fb394502c4e0 で、
記事所有者または staff であることだけを見ていた変更経路がありました。
権限を剥奪した所有者でも自動保存とリビジョン復元から公開本文を変更でき、
公開状態も維持されるため、承認フローを迂回できる状態でした。

### 修正した不変条件

- 全記事変更経路で blog.change_article と対象記事の範囲を同時に確認する
- 公開中の記事は自動保存・リビジョン復元で直接変更しない
- 承認公開は review_article と publish_article の両方を要求する
- 承認は確認時のArticle.versionと結び、自動保存・復元と同じ記事行を
  ロックして、確認後または並行処理中の差し替えを公開しない
- ダッシュボードはCMS権限を要求し、非レビュー担当者は本人の記事だけを集計する
- django-allauth とアプリ側で同じ信頼プロキシ段数を使う
- 公開・承認・削除権限を持つ非staffにもMFAを要求する

さらにHTMLへnonceベースのCSPを付与し、バックアップは0600の
AES-256暗号化ファイルとしてのみ保存し、HMAC-SHA-256で復号前に改ざんを
拒否するようにしました。復旧訓練はDBとmediaの両方を対象にします。Pythonの推移的依存は
ハッシュ付きロックへ、Python/PostgreSQL/Redis/Nginxのイメージは
ダイジェスト固定へ切り替えました。

### 残る本番確認

リポジトリのComposeはNginxでTLS 1.2/1.3を終端し、HTTPをHTTPSへ
リダイレクトする単体構成です。実配備先の証明書、自動更新、ファイアウォール、
CDNを追加する場合のオリジン到達制限は、配備先で別途確認します。本番投入前に実環境で
複数クライアントIP、偽造X-Forwarded-For、HTTPSリダイレクト、
WebAuthn、暗号化バックアップの復元訓練を確認します。

---

## 2026-08-05 Django 5.2.15 → 5.2.17

### 経緯

2026年8月4日、Django Software Foundation が
Django 5.2.17 と 6.0.8 を公開しました。4件の脆弱性が修正されています。

この連載では 1日目に `Django==5.2.15` を固定しました。
5.2.15 は、今回の4件に加えて、
5.2.16 で修正された分（SQL インジェクション・共有キャッシュ・ASGI DoS ほか）
の影響も受けます。

そのため 5.2.17 へ上げます。

### 4件の内容

| CVE | 内容 | Django 評価 | KururuCMS への到達 |
| --- | --- | --- | --- |
| CVE-2026-15307 | GeoDjango の空間検索経由でファイル書込み・SSRF（条件付き RCE） | High | ❌ 到達しない |
| CVE-2026-15337 | 長大な言語コードによるメモリ消費 DoS | Low | ❌ 到達しない |
| CVE-2026-15830 | 入れ子の GeometryCollection で GEOS がクラッシュ | Moderate | ❌ 到達しない |
| CVE-2026-15920 | Django Admin の URLField 表示処理による格納型 XSS | Moderate | ⚠️ **到達しうる** |

### 到達可能性をどう確かめたか

推測ではなく、実際に動いているプロセスへ問い合わせました。

```bash
python manage.py shell --settings=config.settings.local -c "..."
```

```text
Django: 5.2.17
contrib.gis in INSTALLED_APPS: False
DB ENGINE: django.db.backends.sqlite3
set_language URL registered: False
Article の URLField: ['canonical_url']
SiteSetting の URLField: ['base_url']
```

**CVE-2026-15307 と CVE-2026-15830（GeoDjango 系）**

`django.contrib.gis` を `INSTALLED_APPS` に入れていません。
空間フィールドを持つモデルも、GDAL・GEOS を呼ぶコードもありません。

```bash
grep -rn "contrib.gis\|GEOS\|GDAL" --include="*.py" .
```

一致なし。データベースも PostGIS ではありません。
この2件は、KururuCMS の現在の構成では踏みようがありません。

**CVE-2026-15337（言語コードの DoS）**

`django.conf.urls.i18n` を URLconf へ登録していないので、
`set_language` のエンドポイントが存在しません。
URL 解決器から名前を全部集めて確認しました（`set_language URL registered: False`）。

`LANGUAGE_CODE = "ja"` は設定していますが、
利用者が言語コードを送りつけてくる経路がありません。

**CVE-2026-15920（Admin の URLField 格納型 XSS）**

これは**到達しうる**と判断しました。

- Django Admin を7つのアプリで使っている
- `URLField` が2つある（`Article.canonical_url`、`SiteSetting.base_url`）
- どちらも Admin から見える

不正な値が入るには、フォーム検証を通らない経路
（`QuerySet.update()` / `bulk_create()` / データ移行など）が必要ですが、
「今は無いから安全」と言い切るには、
今後そういうコードが1行書かれるだけで崩れます。

個別に監査するより、Django のパッチで塞ぐ方が確実です。

### やったこと

```text
requirements.txt   Django==5.2.15 -> Django==5.2.17
docs/articles/day-01.md   pip install Django==5.2.15 -> 5.2.17
```

### 確認

```text
$ python manage.py test --settings=config.settings.test
Ran 290 tests in 6.465s
OK
```

```text
$ python -c "import django; print(django.get_version())"
5.2.17
```

### この件から言えること

**「LTS だから安全」ではありません。**

Django 5.2 は LTS（長期サポート版）で、2028年4月までサポートされます。
しかしサポートされるのは「5.2 系列の最新パッチ」であって、
5.2.15 が永久に安全でいてくれるわけではありません。

1日目に `Django==5.2.15` と固定したのは、
「昨日は動いたのに今日は動かない」を無くすためでした。
これは今でも正しい判断ですが、**固定したら見に行く責任が生まれます**。
固定は「変わらない」ことを保証するもので、
「安全であり続ける」ことは保証しません。

過去のタグ（`day-01` 〜 `day-10`）は、その日の状態を残すためのものなので
そのままにしてあります。`git checkout day-01` すると
`Django==5.2.15` が出てきます。学習の途中経過としてはそれでよいのですが、
**そのまま公開するものではありません**。
