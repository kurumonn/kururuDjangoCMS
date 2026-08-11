# 【8日目】django-allauth 完全入門――メール認証とワンタイムコードログイン

> 連載「10日で作る Django CMS」の8日目です。
> 成果物: <https://github.com/kurumonn/DjangoCMS>（タグ `day-08`）

---

## 1. 今日の結論

自作の認証を捨てて、django-allauth に置き換えます。

- メールアドレスでのログイン
- メール確認（必須）
- パスワード再設定
- **メールワンタイムコードでのログイン**
- Google・GitHub でのログイン
- ログイン中の端末一覧
- レート制限とアカウント列挙対策

**今日いちばん大事なのは、ワンタイムコードの強度が
「桁数」ではなく「桁数 × 有効期限 × 試行回数 × 発行制限」で決まること**です。

---

## 2. 今日の完成画面

ログイン画面です。メール・ワンタイムコード・ソーシャルがそろっています。

<!-- screenshot: day-08-login.png | ログイン画面 -->

ワンタイムコードの要求画面です。

<!-- screenshot: day-08-login-by-code.png | ワンタイムコードの要求 -->

ユーザー登録画面です。

<!-- screenshot: day-08-signup.png | ユーザー登録 -->

認証の全体像はこうなります。

```text
ログイン方法
├── メールアドレス ＋ パスワード
├── メールワンタイムコード
├── Google
└── GitHub
```

---

## 3. 今日変更するファイル

```text
config/
├── settings.py            変更（allauth の設定を追加）
└── urls.py                変更
accounts/
└── tests.py               新規（設定が意図どおりかを固定）
seo/
└── models.py              変更（Site を同期）
templates/
├── allauth/layouts/base.html   新規（allauth の画面を自サイトへ統合）
├── partials/account_nav.html   新規
└── base.html              変更
static/css/site.css        変更
requirements.txt           変更
```

---

## 4. 完成コード

### 4.1 インストール

```bash
pip install "django-allauth[mfa,socialaccount]"
```

`[mfa]` は9日目で使う TOTP とパスキーのためです。今日入れておきます。

`requirements.txt` では版を固定します。この連載は `65.18.0` です。

```text
django-allauth[mfa,socialaccount]==65.18.0
```

**追記（2026-08-11）: 上流で `65.19.0` が出ています**（2026-08-06 公開）。
`IDP_OIDC_ID_TOKEN_EXPIRES_IN` を設定しても読まれず、
ID トークンの有効期限が常に既定の 300 秒になる、という不具合の修正です。

```python
# 65.18.0
def ID_TOKEN_EXPIRES_IN(self) -> int:
    return 5 * 60                                     # 設定を読んでいない

# 65.19.0
def ID_TOKEN_EXPIRES_IN(self) -> int:
    return self._setting("ID_TOKEN_EXPIRES_IN", 5 * 60)
```

**この CMS は対象外です。** その設定は allauth 自身が OpenID Connect の
Identity Provider（＝トークンを**発行する**側）として動くときのもので、
`allauth.idp.oidc` を `INSTALLED_APPS` に入れて初めて関係します。
この CMS は入れていません。
Google / GitHub ログインは「発行されたトークンを**受け取る**側」なので、別の話です。

自分が対象かどうかは、**設定名の頭を見る**と分かります。
`IDP_OIDC_` で始まるものは IdP 側（発行する側）の設定です。

対象外なので、連載中は `65.18.0` のまま据え置きます。
記事とスクリーンショットがこの版の挙動で書かれており、
版だけ動かすと記事と実物がずれるためです。
判断の理由は `requirements.txt` のコメントにも残しました。

ただし**「対象外だから確認しなくてよい」ではありません**。
確認したうえで対象外だと分かったので据え置く、という順番です。
自分の環境が対象かどうかは、こう調べられます。

```bash
python manage.py shell -c "from django.conf import settings; print('allauth.idp.oidc' in settings.INSTALLED_APPS)"
```

### 4.2 INSTALLED_APPS と MIDDLEWARE

```python
INSTALLED_APPS = [
    ...
    "django.contrib.sitemaps",
    # allauth の「パスキー一覧」「ログイン中の端末」テンプレートが
    # naturaltime フィルタを使う。入れ忘れると、その画面だけ
    # KeyError: 'humanize' で落ちる。
    "django.contrib.humanize",
    # django-allauth（8日目）
    # sites は socialaccount が使う。SITE_ID と合わせて必要。
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    # ログイン中のセッション一覧と、他端末からのログアウト
    "allauth.usersessions",
    ...
]

MIDDLEWARE = [
    ...
    # allauth が必須とするミドルウェア。
    # 入れ忘れると、ログイン処理の途中で必ず例外になる。
    "allauth.account.middleware.AccountMiddleware",
    "allauth.usersessions.middleware.UserSessionsMiddleware",
]

SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    # 管理画面用。ユーザー名とパスワードで認証する。
    "django.contrib.auth.backends.ModelBackend",
    # allauth 用。メールアドレスやソーシャルログインを扱う。
    "allauth.account.auth_backends.AuthenticationBackend",
]
```

### 4.3 アカウントの設定

```python
# ログインはメールアドレスで行う。
# ユーザー名は表示用に残すが、ログインには使わない。
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_UNIQUE_EMAIL = True

# メール確認を必須にする。
# "optional" にすると、他人のメールアドレスで登録して
# そのアドレス宛の通知を受け取れてしまう。
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True
ACCOUNT_CONFIRM_EMAIL_ON_GET = False  # GET で確認を完了させない（メールの先読み対策）

# アカウントの存在を漏らさない。
# 「このメールアドレスは登録されていません」と返すと、
# 総当たりで会員かどうかを調べられる。
ACCOUNT_PREVENT_ENUMERATION = True
ACCOUNT_EMAIL_UNKNOWN_ACCOUNTS = False

# パスワード変更後は他端末のセッションを切る。
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
# ログアウトは POST のみ（GET を許すと強制ログアウトさせられる）。
ACCOUNT_LOGOUT_ON_GET = False

# 重要な操作の前に、もう一度本人確認を求める。
ACCOUNT_REAUTHENTICATION_REQUIRED = True
ACCOUNT_REAUTHENTICATION_TIMEOUT = 300  # 秒
```

### 4.4 メールワンタイムコード

```python
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 180          # 有効期限（秒）
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 3       # 入力の試行回数
ACCOUNT_LOGIN_BY_CODE_MAX_RESEND_COUNT = 3   # 再送の上限

# コードの形式。allauth の既定は "BCDF-GHJK" のような英字8桁。
# ここでは数字6桁にする。スマートフォンで入力しやすく、
# メールからの読み取りミスも減るため。
#
# 数字6桁は 100 万通りしかないので、単体では弱い。
# 次の3つを組み合わせて初めて実用に耐える。
#   1. 有効期限 180 秒（上の TIMEOUT）
#   2. 試行3回で無効化（上の MAX_ATTEMPTS）
#   3. 発行そのものを 5分に3回まで（下の RATE_LIMITS）
# どれか1つでも外すと総当たりが成立するので、一緒に扱う。
ACCOUNT_LOGIN_BY_CODE_FORMAT = {"numeric": True, "length": 6, "dashed": False}
```

### 4.5 レート制限

```python
# 総当たりとメール爆撃を止める。単位は "回/期間"。
# 本番では共有キャッシュ（Redis）が必要。
# ローカルメモリキャッシュだと、Gunicorn のワーカー数だけ制限が緩くなる。
ACCOUNT_RATE_LIMITS = {
    "login": "5/5m",                     # IP ごとのログイン試行
    "login_failed": "5/5m/ip,3/5m/key",  # 失敗回数（アカウント単位も含む）
    "signup": "5/h/ip",
    "send_email": "10/h",
    "change_email": "3/h",
    "reset_password": "5/h/ip,3/h/key",
    "confirm_email": "5/m/key",
    "request_login_code": "3/5m/key",    # ワンタイムコードの発行
}
```

### 4.6 ソーシャルログイン

```python
# 認証情報は環境変数から読む。settings.py に直接書かない。
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
    },
    "github": {
        "APP": {
            "client_id": os.environ.get("GITHUB_CLIENT_ID", ""),
            "secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["user:email"],
    },
}

# 既存アカウントへ自動で紐づけない。
# 自動で繋ぐと、攻撃者が同じメールアドレスのソーシャルアカウントを用意するだけで
# 既存アカウントを乗っ取れる可能性がある。
SOCIALACCOUNT_AUTO_SIGNUP = False
```

### 4.7 URL

```python
urlpatterns = [
    ...
    # allauth.urls は、INSTALLED_APPS の中身を見て
    # socialaccount / mfa / usersessions の URL を自動で足す。
    # それぞれを個別に include してはいけない。
    # 同じ URL 名が二重に登録され、reverse() が後から登録した方を返すため、
    # リンク先が意図しないパスになる。
    path("accounts/", include("allauth.urls")),
    ...
]
```

### 4.8 allauth の画面を自サイトへ統合する

`templates/allauth/layouts/base.html` を置くと、
allauth の全画面がこれを土台にします。

```django
{% extends "base.html" %}
{% comment %}
django-allauth の画面を、このサイトの見た目へ合わせるための土台。

差し替えの要点:

  * allauth の子テンプレートは content ブロックを埋める。
    ここで content を再定義してしまうと名前が衝突するので、
    content には触れず、そのまま base.html へ通す。
  * 代わりに breadcrumb ブロックへアカウント用のナビを差し込む。
  * 認証画面は検索結果に出す必要がないので noindex にする。
{% endcomment %}

{% block title %}{% block head_title %}アカウント{% endblock head_title %} | {{ site_setting.site_name }}{% endblock %}

{% block head_extra %}
  {# ログイン・登録・パスワード再設定の画面を検索結果へ出さない。 #}
  <meta name="robots" content="noindex, nofollow">
  {% block extra_head %}{% endblock extra_head %}
{% endblock %}

{% block breadcrumb %}
  {% if user.is_authenticated %}
    {% include "partials/account_nav.html" %}
  {% endif %}
{% endblock %}
```

### 4.9 メールのサイト名を同期する

```python
# seo/models.py（抜粋）
    def save(self, *args, **kwargs):
        ...
        super().save(*args, **kwargs)
        self._sync_django_site()

    def _sync_django_site(self) -> None:
        """django.contrib.sites の Site を、この設定に合わせる。

        allauth の確認メールは Site からサイト名とドメインを取る。
        同期しないと、メール本文が "example.com" のままになる。
        Site の初期値がそれだからで、画面上はどこにも出ないため気づきにくい。
        """
        from django.contrib.sites.models import Site

        netloc = urlsplit(self.base_url).netloc
        if not netloc:
            return

        Site.objects.update_or_create(
            pk=getattr(settings, "SITE_ID", 1),
            defaults={"domain": netloc, "name": self.site_name},
        )
        # get_current() はキャッシュを持つので、明示的に捨てる。
        Site.objects.clear_cache()
```

---

## 5. コードの意味

### `ACCOUNT_EMAIL_VERIFICATION`

| 値 | 動き |
| --- | --- |
| `"none"` | 確認しない |
| `"optional"` | 確認メールは送るが、未確認でもログインできる |
| `"mandatory"` | 確認するまでログインできない |

`"optional"` の危険性は分かりにくいので補足します。

```text
攻撃者が victim@example.com で登録
   ↓ 確認せずにログインできる
   ↓ そのアカウントで記事を書く
   ↓ 「victim@example.com が投稿しました」という通知が本人へ届く
```

さらに、後から本人が同じアドレスで登録しようとすると、
すでに使われていて登録できません。

### `ACCOUNT_PREVENT_ENUMERATION`

「アカウント列挙」とは、
どのメールアドレスが登録済みかを外から調べることです。

```text
【対策なし】
POST /accounts/password/reset/  email=a@example.com
   → 「そのアドレスは登録されていません」   ← 未登録と分かる

POST /accounts/password/reset/  email=b@example.com
   → 「メールを送信しました」               ← 登録済みと分かる
```

会員名簿が作れてしまいます。
`ACCOUNT_PREVENT_ENUMERATION = True` にすると、
どちらの場合も同じ画面を返します。

### レート制限の書式

```python
"login_failed": "5/5m/ip,3/5m/key",
```

| 部分 | 意味 |
| --- | --- |
| `5/5m` | 5分間に5回まで |
| `/ip` | IP アドレスごとに数える |
| `/key` | 対象（アカウント）ごとに数える |
| `,` | 複数の条件を同時に適用 |

**2つの単位が必要な理由**があります。

```text
IP だけで数える  →  攻撃者が IP を変えれば回避できる
key だけで数える →  1つの IP から大量のアカウントを試せる
```

両方を組み合わせて初めて機能します。

### `ACCOUNT_REAUTHENTICATION_REQUIRED`

重要な操作の前に、もう一度パスワードを求めます。

```text
セッションを盗まれた
   ↓ 攻撃者がログイン状態を持っている
   ↓ しかしパスワードは知らない
   ↓ メールアドレス変更 / パスキー追加 の画面で止まる
```

セッション固定攻撃や、共有 PC でのログイン放置に効きます。

---

## 6. 内部で起きていること

### ワンタイムコードの流れ

```text
メールアドレスを入力
   ↓
サーバーが6桁のコードを生成し、セッションへ保存
   ↓
メール送信
   ↓
利用者がコードを入力
   ↓
有効期限（180秒）を確認
   ↓
試行回数（3回）を確認
   ↓
一致すればログイン成立、コードは無効化
```

### 数字6桁が「弱くない」理由

100 万通りしかないので、単体なら総当たりで破れます。

```text
【制限なし】
1秒に100回試せるなら、100万 ÷ 100 = 10000秒（約3時間）で全通り

【この CMS の設定】
有効期限 180 秒 → その間に試せる回数が上限
試行 3 回で無効 → 実質3通りしか試せない
発行 5分に3回   → コードを作り直して試すこともできない

成功確率 = 3 / 1,000,000 = 0.0003%
```

**桁数を増やすより、回数を絞る方が効きます。**
そして利用者にとっては6桁の方が入力しやすい。

ただし、**3つのうち1つでも外すと成立しません。**
「あとで緩めよう」と思ったときに気づけるよう、まとめてテストで固定します。

```python
def test_login_code_defences_are_all_present(self):
    """数字6桁は単体では弱い。3つの防御がそろって初めて実用に耐える。"""
    self.assertLessEqual(settings.ACCOUNT_LOGIN_BY_CODE_TIMEOUT, 600)
    self.assertLessEqual(settings.ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS, 5)
    self.assertIn("request_login_code", settings.ACCOUNT_RATE_LIMITS)
```

### レート制限とワーカー数

```python
# 本番では共有キャッシュ（Redis）が必要。
# ローカルメモリキャッシュだと、Gunicorn のワーカー数だけ制限が緩くなる。
```

```text
【ローカルメモリキャッシュ + Gunicorn 4ワーカー】
ワーカー1: 5回まで
ワーカー2: 5回まで   ← それぞれ独立して数える
ワーカー3: 5回まで
ワーカー4: 5回まで
合計 20 回試せる

【Redis】
全ワーカーが同じカウンターを見る → 5回まで
```

10日目で Redis を入れます。それまでは開発環境限定の設定です。

---

## 7. コマンドの説明

### `python manage.py migrate`

allauth のテーブルが作られます。

```text
Applying account.0001_initial... OK
Applying socialaccount.0001_initial... OK
Applying sites.0001_initial... OK
Applying usersessions.0001_initial... OK
```

`sites` のマイグレーションは、`example.com` という Site を作ります。
これが後の「メールが example.com になる」問題の原因です。

### メール本文を確認する

開発中は `EMAIL_BACKEND` をコンソールにします。

```python
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
```

ワンタイムコードを要求すると、`runserver` を動かしているターミナルに
メール本文がそのまま出ます。

```text
Subject: [KururuCMS] ログインコード

こんにちは、cms.example.com です!

以下にあなたのログインコードが記載されています。
開いているブラウザのウィンドウに入力してください。

482913
```

**このとき本文を最後まで読んでください。**
「送信されたか」だけを見ていると、サイト名が `example.com` のままでも気づけません。

### URL 名がどこへ解決されるか調べる

```bash
python manage.py shell -c "from django.urls import reverse; print(reverse('usersessions_list'))"
```

```text
/accounts/sessions/
```

`/accounts/` が返ったら、URL を二重に include しています（8.4 参照）。

---

## 8. よくあるエラー

記録は [`docs/errors/day-08.md`](../errors/day-08.md) にあります。
8日目は **同じ罠を2回踏みました**。

### 8.1 `{# ... #}` は1行コメント専用

```text
django.template.exceptions.TemplateSyntaxError:
Unclosed tag on line 9: 'block'. Looking for one of: endblock.
```

「9行目の block が閉じていない」と言われますが、
9行目には `{% block %}` を **書いていません**。

**原因**: 説明のために書いた複数行コメントの中に、
`{% block content %}` という文字列が入っていました。

```django
{# django-allauth の画面を差し替えるための土台。

   * allauth の子テンプレートは {% block content %} を埋める。   ← ここ
#}
```

Django の `{# ... #}` は **1行コメント専用**です。
複数行にまたがると、2行目以降はコメントとして扱われず、
中に書いたタグが本物として解釈されます。

**対処**: `{% comment %} ... {% endcomment %}` を使います。

### 8.2 同じ罠をもう一度踏んだ

上を直した直後、別のテンプレートで **まったく同じことをしました**。

```text
django.template.exceptions.TemplateSyntaxError:
'url' takes at least one argument, a URL pattern name.
```

`partials/account_nav.html` の冒頭コメントに、
「`{% url %}` を直接書くと落ちます」という説明文が入っていました。

**教訓**: 「テンプレートの書き方を説明するコメント」は、
書いた説明そのものが実行されうる、という点で特別です。
`{% comment %}` を既定にしてしまうのが安全です。

### 8.3 `{% extends %}` は最初のタグでなければならない

```text
django.template.exceptions.TemplateSyntaxError:
{% extends "base.html" %} must be the first tag in 'allauth/layouts/base.html'.
```

**8.1 と 8.3 は互いに引っ張り合います。**

- 複数行コメントにしたい → `{% comment %}` を使う必要がある
- `{% comment %}` はタグ → `{% extends %}` より前に置けない

結果として「extends を1行目、説明はその下」という形に落ち着きます。
最初からこの順序で書いていれば、両方とも踏みませんでした。

### 8.4 allauth の URL を二重に include する

**症状**: テストは通っているのに、画面上の「ログイン中の端末」リンクが
`/accounts/sessions/` ではなく `/accounts/` を指している。

**原因**: `allauth.urls` は `INSTALLED_APPS` を見て
`usersessions` の URL を **自動で足します**。
個別に include すると、同じ URL 名が2回登録され、
`reverse()` は後から登録された方を返します。

**なぜテストで気づけなかったか**: どちらの URL でもビューは同じです。
食い違うのは **リンクの文字列** だけで、動作は変わりません。

**対処**: 個別の include を消し、URL 名の解決先をテストで固定します。

```python
def test_usersessions_url_is_under_sessions(self):
    self.assertEqual(reverse("usersessions_list"), "/accounts/sessions/")
```

### 8.5 ワンタイムコードの形式が想定と違う

```text
AssertionError: '' is not true : メール本文にコードが見つからない
```

テストは6桁の数字を探していましたが、届いたのは `SHMK-ZHHG` でした。

**原因**: allauth の既定は英字8桁（4桁ずつハイフン区切り）です。
使う文字は `BCDFGHJKLMNPQRSTVWXZ` の20種類に限られています
（RFC 8628 に沿って、`0` と `O`、`1` と `I` のような紛らわしい組を除いてある）。

**対処**: 形式を明示します。桁数を減らすなら、防御をそろえてください（「6.」参照）。

### 8.6 確認メールの差出人が `example.com` のまま

```text
こんにちは、example.com です!
```

**原因**: allauth のメールは `django.contrib.sites` の `Site` から
サイト名とドメインを取ります。初期値が `example.com` です。

**気づきにくい理由**:

- 画面上はどこにも `example.com` が出ない
- テストも通る（メールの本文まで見ていなかった）
- 本番で気づいたときには、その文面のメールが既に配信済み

**対処**: 「4.9」を参照してください。
`Site.objects.clear_cache()` を忘れると、
同じプロセスの中では古い値が返り続けます。

**メールは、実際に本文を読むまで確認したことになりません。**

---

## 9. 動作確認

### ログイン

- [ ] `/accounts/login/` が自サイトのデザインで表示される
- [ ] メールアドレスとパスワードでログインできる
- [ ] ユーザー名では **ログインできない**（`ACCOUNT_LOGIN_METHODS = {"email"}`）
- [ ] 認証画面に `<meta name="robots" content="noindex, nofollow">` が出る
- [ ] ログアウトが POST になっている（GET では確認画面が出るだけ）

### 登録とメール確認

- [ ] 登録すると確認メールが送られる
- [ ] 登録直後は **ログインしていない状態** になる
- [ ] 登録済みのアドレスで登録しても「既に登録済み」と言われない
- [ ] 12文字未満のパスワードが拒否される

### ワンタイムコード

- [ ] `/accounts/login/code/` でコードを要求できる
- [ ] メール本文に6桁の数字が入っている
- [ ] **メール本文のサイト名が `example.com` になっていない**
- [ ] 正しいコードでログインできる
- [ ] 間違ったコードを3回入れると、正しいコードでも通らなくなる
- [ ] 未登録のアドレスを入れても「登録されていません」と言われない
- [ ] 未登録のアドレスへメールが送られない
- [ ] 5分間に4回コードを要求すると、4回目のメールが送られない

### レート制限

- [ ] 間違ったパスワードで10回試すと 429 が返る
- [ ] その後、正しいパスワードでもログインできない

---

## 10. セキュリティ上の注意

### 認証情報を settings.py に書かない

```python
"client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
"secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
```

`settings.py` は Git に入ります。
一度コミットすると、履歴から完全に消すのは非常に面倒です。

**すでにコミットしてしまった場合は、鍵の削除ではなく再発行をしてください。**
履歴を書き換えても、フォークやキャッシュには残ります。

### `SOCIALACCOUNT_AUTO_SIGNUP = False`

自動で既存アカウントへ紐づけると、こうなります。

```text
攻撃者が victim@example.com の GitHub アカウントを用意する
   （GitHub 側でメール確認が甘いプロバイダーだと成立しうる）
   ↓ 「GitHub でログイン」
   ↓ 同じメールアドレスなので既存アカウントへ接続
   ↓ victim のアカウントでログイン完了
```

自動接続を切ると、既存アカウントへの紐づけには
**そのアカウントへのログイン** が必要になります。

### ログアウトを GET で許さない

```python
ACCOUNT_LOGOUT_ON_GET = False
```

GET で許すと、外部サイトにこれを置くだけで強制ログアウトさせられます。

```html
<img src="https://cms.example.com/accounts/logout/">
```

実害は小さく見えますが、
「入力途中のフォームを消す」「操作を妨害する」といった嫌がらせに使えます。

### パスワード変更で他端末を切る

```python
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True
```

パスワードを変える動機の多くは「盗まれたかもしれない」です。
変更後も攻撃者のセッションが生きていては意味がありません。

### レート制限は本番で共有キャッシュにする

ローカルメモリキャッシュのままだと、
Gunicorn のワーカー数だけ制限が緩くなります。
10日目で Redis を入れます。

### 認証画面を検索結果に出さない

```django
<meta name="robots" content="noindex, nofollow">
```

ログイン画面が検索結果に出ると、フィッシングの参考にされます。
また、パスワード再設定リンクを含むページが誤ってクロールされる事故も防げます。

---

## 11. 今日の復習問題

**問1.** `ACCOUNT_EMAIL_VERIFICATION` を `"optional"` にすると、
どのような問題が起きますか。

**問2.** 「アカウント列挙」とは何ですか。
パスワード再設定の画面で、どのように防ぎますか。

**問3.** レート制限を IP 単位とアカウント単位の両方で掛ける理由を説明してください。

**問4.** 数字6桁のワンタイムコードを実用に耐えるものにするために、
必要な3つの設定を挙げ、それぞれが無い場合に何が起きるか答えてください。

**問5.** ソーシャルログインで `SOCIALACCOUNT_AUTO_SIGNUP = False` にする理由は何ですか。

<details>
<summary>解答</summary>

**問1.**
他人のメールアドレスで登録して、そのままログインできてしまいます。
そのアカウントで活動すると、本人へ身に覚えのない通知が届きます。
また、本人が後から同じアドレスで登録しようとしても、
すでに使われていて登録できません。

**問2.**
どのメールアドレスが登録済みかを、外部から調べる行為です。
「そのアドレスは登録されていません」と「メールを送信しました」で
応答が違うと、その差から会員かどうかが分かります。
`ACCOUNT_PREVENT_ENUMERATION = True` にして、
どちらの場合も同じ応答を返します。

**問3.**
IP 単位だけだと、攻撃者が IP を変えるだけで回避できます。
アカウント単位だけだと、1つの IP から大量のアカウントを試せます。
両方を同時に掛けることで、どちらの攻撃も止められます。

**問4.**
有効期限（180秒）・試行回数（3回）・発行制限（5分に3回）の3つです。
有効期限が無いと、時間をかけて総当たりできます。
試行回数の制限が無いと、短時間に大量のコードを試せます。
発行制限が無いと、コードを作り直して何度でも挑戦できます。

**問5.**
同じメールアドレスのソーシャルアカウントを用意されただけで、
既存アカウントへ接続してログインできてしまう可能性があるためです。
自動接続を切ると、紐づけにそのアカウントへのログインが必要になります。

</details>

---

## 12. Git の差分

```text
タグ    : day-08
コミット: day-08: django-allauth・メール確認・ワンタイムコードログインを導入
```

```bash
git diff day-07 day-08
```

設定だけを見る場合はこちらです。

```bash
git show day-08 -- config/settings.py
```

---

## 13. 次回予告

9日目は、パスワードに依存しない認証を足します。

- TOTP（認証アプリ）
- リカバリコード
- **パスキー（WebAuthn）**
- 管理者への多要素認証の必須化
- 本番で危険な設定を起動時に検出するシステムチェック

「テストで確かめる」だけでは足りない理由と、
`manage.py check --deploy` に検査を載せる方法を扱います。

次回 → [【9日目】Django でパスキー認証](day-09.md)
