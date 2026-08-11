# 【9日目】Django でパスキー認証――TOTP・WebAuthn・復旧方法まで実装

> 連載「10日で作る Django CMS」の9日目です。
> 成果物: <https://github.com/kurumonn/DjangoCMS>（タグ `day-09`）

---

## 1. 今日の結論

パスワードに依存しない認証を足します。

- TOTP（認証アプリ）
- リカバリコード
- **パスキー（WebAuthn）**
- 管理者への多要素認証の必須化
- 本番で危険な設定を **起動時に** 検出するシステムチェック

**今日いちばん大事なのは、復旧手段を必ず用意すること**です。
認証を強くするほど、失ったときに戻れなくなります。

そしてもう1つ。**多要素認証は「設定した」だけでは効きません。**

この記事は最初、管理者の判定を「認証手段を登録しているか」だけで書いていました。
公開前の見直しで、**その判定をすり抜ける経路が2つ**見つかりました。

| すり抜けた経路 | 何が起きていたか |
| --- | --- |
| パスキー1本だけでログイン | 2段目の認証が丸ごと飛ぶ。しかも生体認証・PIN の確認は保証されない |
| Django 標準の管理画面ログイン | allauth を通らないので、パスワードだけで `/admin/` に入れた |

どちらも「登録済みか」しか見ていなかったことが原因です。
**そのセッションが何によって成立したか**まで見る必要がありました。
6章で、なぜ気づきにくいのかを含めて書きます。

---

## 2. 今日の完成画面

多要素認証の一覧です。

<!-- screenshot: day-09-mfa-index.png | 多要素認証の一覧 -->

TOTP の設定画面です。QR コードが出ます。

<!-- screenshot: day-09-totp-activate.png | TOTPの設定 -->

> このスクリーンショットに映っているシークレットは、
> 手元の SQLite にしか存在しないデモ用アカウントのものです。
> **本番の画面をそのまま記事へ載せないでください。**
> QR コードとシークレットは、それ単体で認証を突破できる情報です。

最終的な認証構成はこうなります。

```text
ログイン
├── パスワード
├── メールワンタイムコード
├── Google・GitHub
└── パスキー（単独でログイン可能）

追加認証（パスワードログインの後）
├── TOTP
└── パスキー

復旧
├── リカバリコード
├── 別の登録済みパスキー
└── 管理者による本人確認
```

---

## 3. 今日変更するファイル

```text
config/settings.py         変更（MFA の設定 / humanize）
config/urls.py             変更（管理画面のログインを allauth へ差し替え）
accounts/
├── middleware.py          新規（管理者へのMFA必須化）
├── checks.py              新規（本番の危険設定を検出）
├── apps.py                変更（チェックを登録）
├── testing.py             新規（本番と同じ経路でログインするヘルパー）
└── tests_mfa.py           新規
blog/
├── views.py               変更（編集者の権限を修正）
└── tests/factories.py     変更
dashboard/api.py           変更（権限判定を共有）
tools/capture_screenshots.py  新規（記事用スクショの自動撮影）
```

---

## 4. 完成コード

### 4.1 MFA の設定

```python
INSTALLED_APPS = [
    ...
    "allauth.usersessions",
    # 多要素認証（9日目）: TOTP / リカバリコード / パスキー
    "allauth.mfa",
    ...
]

# 3種類を有効にする。用途が違うので、どれか1つでは足りない。
#
#   totp           … スマートフォンの認証アプリ。端末を持っていれば使える。
#   recovery_codes … 認証アプリを失ったときの最後の手段。紙に印刷して保管する。
#   webauthn       … パスキー。端末の生体認証や物理キー。フィッシングに強い。
#
# recovery_codes を外すと、スマートフォンを失くした利用者が
# 二度とログインできなくなる。必ず入れる。
MFA_SUPPORTED_TYPES = ["totp", "recovery_codes", "webauthn"]

# パスキーだけでログインできるようにする（パスワード入力なし）。
MFA_PASSKEY_LOGIN_ENABLED = True
# 登録時のパスキー作成は無効のまま。
# 最初からパスキーだけで作らせると、その端末を失った時点で復旧手段が無くなる。
MFA_PASSKEY_SIGNUP_ENABLED = False

MFA_TOTP_ISSUER = os.environ.get("DJANGO_MFA_ISSUER", "KururuCMS")
MFA_TOTP_PERIOD = 30
MFA_TOTP_DIGITS = 6
# 時計のずれを吸収する幅（秒）。広げすぎると総当たりが楽になる。
MFA_TOTP_TOLERANCE = 30

MFA_RECOVERY_CODE_COUNT = 10
MFA_RECOVERY_CODE_DIGITS = 8
# リカバリコードは発行時に一度だけ見せる。
# 後からいつでも見られる状態にすると、画面を覗かれただけで突破される。
MFA_RECOVERY_CODES_SHOW_ONCE = True

# WebAuthn は HTTPS でしか動かない（localhost は例外扱い）。
#
# 「DEBUG と同じ値にしておけば安全」では不十分。
# DEBUG は環境変数の書き忘れで True のまま本番へ出ることがあり、
# そのとき WebAuthn の保護まで一緒に外れてしまう。
# 独立した環境変数にしたうえで、accounts/checks.py の
# システムチェックで「本番なのに有効」を検出する。
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = (
    os.environ.get("DJANGO_MFA_ALLOW_INSECURE_ORIGIN", "1" if DEBUG else "0") == "1"
)

# 管理画面へ入れる利用者には多要素認証を必須にする。
# 記事を書くだけの利用者にまで強制すると運用が回らないため、対象を絞る。
MFA_REQUIRED_FOR_STAFF = os.environ.get("DJANGO_MFA_REQUIRED_FOR_STAFF", "1") == "1"
```

### 4.2 管理者への必須化ミドルウェア

**詰まないように作ること**と、**判定を2段構えにすること**が要点です。

```python
# accounts/middleware.py（抜粋）

# パスキーとは独立した要素として数えるログイン方法。
# ここに載っていない方法は数えない（安全側）。
INDEPENDENT_LOGIN_METHODS = frozenset({
    "password",        # パスワード（知識）
    "password_reset",  # 再設定リンク（メールボックスの所持）
    "code",            # メールのワンタイムコード（同上）
    "socialaccount",   # 外部プロバイダー（そちらで認証済み）
})


class StaffMfaRequiredMiddleware:
    """管理画面へ入れる利用者に、多要素認証を求める。

    判定は2段構え。

      1. 登録 … 認証手段を1つ以上登録しているか
      2. 成立 … いま使っているセッションが、いくつの要素で成立したか

    実装で気を付けること:

      * 設定画面そのものを塞がない（塞ぐと設定しに行けない）
      * ログアウトを塞がない（塞ぐと抜け出せない）
      * 再認証の画面を塞がない（追加要素を求める先が塞がると往復する）
      * 静的ファイルを塞がない（CSS が当たらず画面が崩れる）

    1つでも通し忘れると、利用者が詰む。
    """

    EXEMPT_URL_NAMES = frozenset({
        "mfa_index",
        "mfa_activate_totp",
        "mfa_view_recovery_codes",
        "mfa_generate_recovery_codes",
        "mfa_download_recovery_codes",
        "mfa_list_webauthn",
        "mfa_add_webauthn",
        "mfa_reauthenticate",
        "mfa_authenticate",
        "account_logout",
        "account_reauthenticate",
        ...
    })

    def _has_authenticator(self, user) -> bool:
        """【1段目】日常的に使える認証手段を登録しているか。

        リカバリコードは「他の手段を失ったときの控え」であって、
        日常の認証手段ではない。これだけでは設定済みとみなさない。
        """
        from allauth.mfa.models import Authenticator

        return (
            Authenticator.objects.filter(user=user)
            .exclude(type=Authenticator.Type.RECOVERY_CODES)
            .exists()
        )

    @staticmethod
    def _has_independent_factor(request) -> bool:
        """【2段目】このセッションが、パスキー以外の要素も通って成立したか。

        allauth はログインの過程で通った方法をセッションへ書き残す
        （`account_authentication_methods`）。その記録を読む。

        パスキー（webauthn）は数えない。認証時に UV（生体認証・PIN の確認）を
        強制できない以上、「その認証器を持っている」以上のことを
        確認できていないため。2本目のパスキーでも、同じ鍵を
        もう一度触っても、要素は増えない。

        記録が空のセッションも通さない。allauth を経由していない
        ログインを黙って素通りさせないため。
        """
        from allauth.account.authentication import get_authentication_records
        from allauth.mfa.models import Authenticator

        for record in get_authentication_records(request):
            method = record.get("method")
            if method == "mfa":
                if record.get("type") != Authenticator.Type.WEBAUTHN:
                    return True  # 認証アプリ・リカバリコード
            elif method in INDEPENDENT_LOGIN_METHODS:
                return True
        return False
```

2段目に引っかかった管理者は、追加の本人確認へ送ります。
`?next=` を付けて、確認のあとに元のページへ戻れるようにします。

```python
if not self._has_independent_factor(request):
    messages.warning(
        request,
        "管理者権限の画面へ進むには、もう一度本人確認が必要です。"
        "パスキーだけでは、その端末を持っていることしか確認できません。",
    )
    target = self._reauthentication_url(user)
    return redirect(f"{target}?{urlencode({'next': request.get_full_path()})}")
```

### 4.3 管理画面のログイン画面を差し替える

**ここを開けたままだと、上のミドルウェアは意味を失います。**

```python
# config/urls.py（抜粋）
urlpatterns = [
    # URL は上から順に照合される。admin.site.urls より**前**に置く。
    # 後ろに置くと admin 側が先に一致して、この行は一生使われない。
    path(
        f"{settings.ADMIN_URL_PATH}/login/",
        RedirectView.as_view(pattern_name="account_login", query_string=True),
        name="admin_login_redirect",
    ),
    path(f"{settings.ADMIN_URL_PATH}/", admin.site.urls),
    ...
]
```

`query_string=True` を落とすと `?next=...` が消え、
ログイン後に目的のページではなくトップへ戻されます。

### 4.4 本番で危険な設定を検出するシステムチェック

```python
# accounts/checks.py（抜粋）
"""本番で危険になる設定を、起動時に検出する。

なぜテストではなくシステムチェックなのか。

    テスト          … 開発者が manage.py test を打ったときだけ実行される
    システムチェック … runserver でも migrate でも check --deploy でも必ず走る

「本番で危険な設定になっていないか」は、
実行し忘れようがない場所に置く。
"""

from django.conf import settings
from django.core.checks import Error, Warning, register


@register(deploy=True)
def check_mfa_settings(app_configs, **kwargs):
    """多要素認証まわりの設定を検査する。"""
    if settings.DEBUG:
        # 開発中は何も言わない。邪魔をしないことも要件のうち。
        return []

    issues = []

    if getattr(settings, "MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN", False):
        issues.append(Error(
            "MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN が本番で有効になっています。",
            hint="環境変数 DJANGO_MFA_ALLOW_INSECURE_ORIGIN=0 を設定してください。"
                 "有効なままだと、HTTPS でない経路でもパスキーの登録・認証を"
                 "受け付けてしまいます。",
            id="accounts.E001",
        ))

    if "recovery_codes" not in getattr(settings, "MFA_SUPPORTED_TYPES", []):
        issues.append(Warning(
            "リカバリコードが無効になっています。",
            hint="認証アプリやパスキーを失った利用者が、"
                 "二度とログインできなくなります。",
            id="accounts.W001",
        ))

    return issues
```

### 4.5 編集者の権限を直す

**9日目にスクリーンショットを撮ろうとして見つけた不具合です。**

```python
# blog/views.py
def _can_edit(user, article: Article) -> bool:
    """記事を編集・削除してよいか。

    「他人の記事も編集してよい人」の判定に is_staff だけを使わないこと。
    is_staff は「Django の管理画面へ入れる」という意味であって、
    「編集者である」という意味ではない。

    この CMS では、編集者ロールに blog.review_article を与えている。
    レビューして公開する役目である以上、本文を直せなければ仕事にならない。
    is_staff だけを見ていると、編集者が他人の記事を開いた瞬間に 403 になる。
    """
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    # 編集者（レビュー権限を持つ人）は、どの記事でも編集できる。
    if user.has_perm("blog.review_article"):
        return True
    return article.author_id == user.pk
```

### 4.6 スクリーンショットの自動撮影

```python
# tools/capture_screenshots.py（抜粋）
"""記事に載せるスクリーンショットを撮る。

なぜスクリプトにするか:

  * 手で撮ると、記事を書き直すたびに画面と食い違っていく
  * ウィンドウ幅や配色がばらつくと、記事の見た目がそろわない
  * 撮り直しが一発でできると、UI を直すことへの心理的な抵抗が減る
"""

        # ログイン前と後で、ブラウザーの状態（Cookie）を分ける。
        #
        # 1つのコンテキストで使い回すと、ログイン後に
        # /accounts/login/ を開いてもダッシュボードへリダイレクトされ、
        # 「ログイン画面のつもりがダッシュボードの写真」になる。
        # 実際にこれをやって、4枚が同じ画像になった。
        anon_context = new_context()
        auth_context = new_context()


def _warn_about_duplicates() -> None:
    """同じ内容の画像が複数ないか確かめる。

    リダイレクトで別のページを撮ってしまうと、
    ファイル名は違うのに中身が同じ画像ができる。
    見た目では気づきにくいので、ハッシュで検出する。
    """
```

---

## 5. コードの意味

### TOTP の仕組み

```text
【登録時】
サーバーが共有秘密鍵を生成
   ↓ QR コードで表示
スマートフォンの認証アプリが読み取って保存

【ログイン時】
アプリ側:    秘密鍵 + 現在時刻 → 6桁のコード
サーバー側:  秘密鍵 + 現在時刻 → 6桁のコード
             ↓
          一致すれば認証成功
```

**通信していません。** 両者が同じ計算を独立に行い、結果を突き合わせるだけです。
だから機内モードでも動きます。

計算式は次のとおりです。

```python
counter = int(time.time()) // 30        # 30秒ごとに1つ進む
code = HMAC-SHA1(secret, counter) の下位6桁
```

### `MFA_TOTP_TOLERANCE`

```python
MFA_TOTP_TOLERANCE = 30   # 秒
```

端末とサーバーの時計は完全には一致しません。
許容が 0 だと、数秒ずれただけでログインできなくなります。

ただし広げすぎると、**同時に有効なコードが増えます**。

```text
許容 30 秒  → 前後1個ずつ、合計3個のコードが有効
許容 300 秒 → 前後10個ずつ、合計21個のコードが有効（7倍通りやすい）
```

### `MFA_RECOVERY_CODES_SHOW_ONCE`

```python
MFA_RECOVERY_CODES_SHOW_ONCE = True
```

`False` にすると、ログイン中はいつでもリカバリコードを見られます。
一見便利ですが、**セッションを盗まれた時点で全コードが漏れます**。

`True` なら、発行時にしか表示されません。
控え忘れたら再発行（古いコードは無効）になります。

### パスキー（WebAuthn）の仕組み

```text
【登録時】
端末が鍵ペアを作る
   ├── 秘密鍵: 端末から出ない（Secure Enclave / TPM など）
   └── 公開鍵: サーバーへ送る

【ログイン時】
サーバー → チャレンジ（ランダムな値）を送る
端末     → 生体認証などで本人確認 → 秘密鍵で署名
サーバー → 公開鍵で署名を検証
```

**フィッシングに強い理由**がここにあります。

署名の対象には、**アクセスしているドメイン名が含まれます**。

```text
本物:   cms.example.com  → cms.example.com 向けの署名
偽物: cms-example.com    → cms-example.com 向けの署名
                            ↓
                     本物のサーバーでは検証に失敗する
```

利用者が偽サイトに騙されても、**署名が使い回せません**。
パスワードや TOTP のコードは、偽サイトへ入力すれば本物へ中継されてしまいます。

**ただし「フィッシングに強い」と「1本で2要素ぶん」は別の話です。**

図の「生体認証などで本人確認」の部分は、**保証されていません**。
WebAuthn ではこれを UV（User Verification）と呼び、
サーバーは要求の強さを3段階で指定します。

| 指定 | 意味 | サーバーは検査するか |
| --- | --- | --- |
| `required` | 生体認証か PIN を必ず確認すること | する。無ければ失敗 |
| `preferred` | できれば確認してほしい | **しない** |
| `discouraged` | 確認しなくてよい | しない |

allauth が**認証時**に使うのは `preferred` です（`begin_authentication`）。
そして fido2 は `required` のときしか UV フラグを検査しません
（`Fido2Server.authenticate_complete`）。

```text
登録時   … user_verification=REQUIRED    ← 確認される
認証時   … user_verification=PREFERRED   ← 確認されるとは限らない
```

つまり **PIN を設定していないセキュリティキーなら、拾って挿すだけで通ります**。
スマートフォンや PC の内蔵認証器は事実上いつも UV を行うので、
実際に困るのは主に外付けキーですが、
「登録時に確認したから認証時も安全」は成り立ちません。

だからこの CMS では、パスキー1本を **1要素（持っていること）** として数えます。
記事を読むだけならそれで十分です。
管理画面のように権限が強い場所だけ、もう1要素を求めます。

### `@register(deploy=True)`

```python
@register(deploy=True)
def check_mfa_settings(app_configs, **kwargs):
```

| 書き方 | 実行されるタイミング |
| --- | --- |
| `@register()` | `runserver` `migrate` `check` すべて |
| `@register(deploy=True)` | `check --deploy` のときだけ |

本番向けの検査は `deploy=True` にします。
開発中に毎回警告が出ると、無視する習慣がついてしまうためです。

---

## 6. 内部で起きていること

### 3種類の認証手段の役割分担

| 手段 | 強さ | 失いやすさ | 位置づけ |
| --- | --- | --- | --- |
| TOTP | 中 | 端末の紛失・機種変更 | 日常の2段目 |
| パスキー | 高（フィッシング耐性） | 端末の紛失 | 日常の1段目にもなれる |
| リカバリコード | 中 | 紙の紛失 | **最後の手段** |

**リカバリコードを日常の認証手段とみなさない**のが重要です。

```python
def _has_mfa(self, user) -> bool:
    """日常的に使える認証手段を登録しているか。

    リカバリコードは「他の手段を失ったときの控え」であって、
    日常の認証手段ではない。これだけでは設定済みとみなさない。
    """
    return (
        Authenticator.objects.filter(user=user)
        .exclude(type=Authenticator.Type.RECOVERY_CODES)
        .exists()
    )
```

リカバリコードだけを登録した状態を「設定済み」と認めると、
利用者は10枚の紙を毎回持ち歩くことになります。

### 必須化ミドルウェアで詰まないようにする

```text
【通し忘れた場合】
管理者がログイン
   ↓ ミドルウェアが mfa_index へリダイレクト
   ↓ mfa_index も塞いでいる
   ↓ また mfa_index へリダイレクト
   ↓ 無限ループ（ERR_TOO_MANY_REDIRECTS）
```

通すべきものは4種類あります。

| 通すべきもの | 通さないと |
| --- | --- |
| 多要素認証の設定画面 | 設定しに行けない（無限リダイレクト） |
| ログアウト | 抜け出せない |
| 再認証の画面 | 設定画面の手前で止まる |
| 静的ファイル | CSS が当たらず画面が崩れる |

テストで固定します。

```python
def test_staff_can_still_reach_mfa_setup(self):
    """設定ページ自体を塞ぐと、設定しに行けなくなる。"""

def test_staff_can_still_log_out(self):
    """ログアウトを塞ぐと詰む。"""
```

### パスキー単独ログインで、要素が静かに1つに減る

`MFA_PASSKEY_LOGIN_ENABLED = True` にすると、パスキー1本でログインが完了します。
便利です。ただし、このとき allauth は**2段目の認証を丸ごと飛ばします**。

```python
# allauth/mfa/stages.py（抜粋）
def _should_handle(self, request) -> bool:
    ...
    if did_use_passwordless_login(request):
        return False        # ← 追加認証をしない
```

これは仕様として正しい判断です。
パスキーが UV 込みで使われていれば、それ自体が2要素だからです。
問題は、**UV 込みかどうかを確かめていない**ことでした（5章）。

そして、当初のミドルウェアはこう書いていました。

```python
return not self._has_authenticator(user)   # 登録しているか、だけ
```

登録は済んでいます。だから通ります。

```text
パスキーを拾う
   ↓ ログイン（PIN 無しでも通る）
   ↓ 2段目の認証は飛ばされる
   ↓ ミドルウェア「認証手段は登録済みですね」→ 通過
管理画面
```

**「多要素認証を必須にした」と書いたコードが、1要素で通していました。**

直し方は、セッションが何で成立したかを見ることです。
allauth はログインの過程を `account_authentication_methods` に残しています。

```python
# パスキー単独ログインの直後
[{"method": "mfa", "type": "webauthn", "passwordless": True, "id": 3, "at": ...}]

# パスワード＋TOTP でログインした直後
[{"method": "password", "at": ...},
 {"method": "mfa", "type": "totp", "id": 1, "at": ...}]
```

前者は要素が1つ、後者は2つ。この差を読めば判定できます。

同じ鍵をもう一度触っても要素は増えない、という点も大事です。
再認証の画面にはパスキーの選択肢も出るので、
「もう一度タップすれば通る」なら対策は形だけになります。
テストで固定しました。

```python
def test_tapping_a_passkey_again_is_not_a_second_factor(self):
    """同じ種類の要素を2回通しても、要素は1つのまま。"""
```

### 管理画面のログイン画面は allauth を通らない

こちらのほうが影響は大きいものでした。

`admin.site.urls` には **admin 自身のログイン画面**が含まれています。
これは allauth のログインフローとは別物で、ログインステージを一切通りません。

```text
/accounts/login/     → allauth → ログインステージ → 2段目の認証 → 完了
/admin/login/        → Django の LoginView → 完了
                                              ↑ 2段目が無い
```

TOTP もパスキーも登録済みのスーパーユーザーで実際に試すと、こうなりました。

```text
POST /admin/login/  →  302 /admin/
GET  /admin/        →  200
session keys: ['_auth_user_backend', '_auth_user_hash', '_auth_user_id']
```

`account_authentication_methods` がありません。allauth を一度も通っていません。
**パスワードだけで管理画面に入れていました。**

しかも認証バックエンドは allauth のものが効いているので、
ユーザー名の欄にメールアドレスを入れても通ります。

対策は 4.3 のとおり、admin のログイン画面を allauth のログインへ差し替えることです。
`admin.site.urls` より前に置くのが要点で、後ろに置くと一生使われません。

この2つには共通点があります。
**どちらもテストが通っていて、画面も正常に動いていました。**
「多要素認証を有効にした」ことは確認していて、
「有効にした認証を回避できないか」を確認していなかった、という差です。

### なぜ `DEBUG` に紐づけないのか

最初はこう書いていました。

```python
# 開発中だけ緩め、本番では必ず False にする
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = DEBUG
```

問題が2つあります。

**問題1: DEBUG の書き忘れで保護まで一緒に外れる**

```python
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"   # 既定は True
```

本番で `DJANGO_DEBUG=0` を設定し忘れると、
`DEBUG=True` になるだけでなく、**WebAuthn の保護も同時に外れます**。
1つの設定ミスが2つの穴になります。

**問題2: テストで確かめられない**

Django のテストランナーは実行時に `settings.DEBUG` を `False` へ書き換えますが、
`MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN` は
**settings.py を読み込んだ時点**で計算済みなので `True` のまま残ります。

```text
AssertionError: True is not false
```

このテストは「本番が安全か」を確かめているつもりで、何も確かめていません。

**解決**: 独立した環境変数にし、システムチェックで検出します。

```bash
DJANGO_DEBUG=0 python manage.py check --deploy
```

```text
ERRORS:
?: (accounts.E002) メールの送信先がコンソールのままです。
	HINT: メール確認・ワンタイムコード・パスワード再設定が利用者へ届きません。
```

---

## 7. コマンドの説明

### `python manage.py migrate`

```text
Applying mfa.0001_initial... OK
Applying mfa.0002_authenticator_timestamps... OK
Applying mfa.0003_authenticator_type_uniq... OK
```

`mfa_authenticator` テーブルが作られます。
TOTP の秘密鍵、パスキーの公開鍵、リカバリコードがすべてここに入ります。

### `python manage.py check --deploy`

| 項目 | 内容 |
| --- | --- |
| 目的 | 本番向けの設定を検査する |
| 実行場所 | `manage.py` があるディレクトリ |
| 正常例 | `System check identified no issues` |
| 異常例 | `accounts.E001` `security.W004` など |
| 判断方法 | ERRORS が0件であること |

`DEBUG=False` の状態で実行しないと意味がありません。

```bash
DJANGO_DEBUG=0 DJANGO_SECRET_KEY=... python manage.py check --deploy
```

Windows の PowerShell では次のようにします。

```powershell
$env:DJANGO_DEBUG="0"; python manage.py check --deploy
```

### `python tools/capture_screenshots.py`

| 項目 | 内容 |
| --- | --- |
| 目的 | 記事用のスクリーンショットを撮り直す |
| 前提 | 開発サーバーが起動していること、playwright が入っていること |
| 正常例 | `11 枚を docs/images へ保存しました。` |
| 異常例 | `警告: 内容が同じ画像があります -> a.png, b.png` |
| 判断方法 | 重複の警告が出ないこと |

```bash
pip install playwright
```

```bash
python -m playwright install chromium
```

---

## 8. よくあるエラー

記録は [`docs/errors/day-09.md`](../errors/day-09.md) にあります。

### 8.1 `KeyError: 'humanize'` でパスキー一覧だけが落ちる

**原因**: allauth のパスキー一覧テンプレートが `naturaltime` フィルタを使います。
`django.contrib.humanize` を `INSTALLED_APPS` に入れていないと、
**その画面だけ** 500 になります。

**気づきにくい理由**: `manage.py check` は通ります。
テンプレートは描画時に初めて読まれるためです。

**対処**: `INSTALLED_APPS` に `"django.contrib.humanize"` を追加します。

### 8.2 `force_login()` では再認証が要る画面を開けない

```text
AssertionError: 302 != 200
```

**原因**: `ACCOUNT_REAUTHENTICATION_REQUIRED = True` にしているため、
認証手段を追加・削除する画面は **直前にパスワードを入力したこと** を求めます。
`force_login()` はセッションへユーザーを入れるだけで、
「いつ本人確認したか」を記録しません。

**これはバグではなく、意図した動作です。**
セッションを盗まれても、パスワードを知らなければ
攻撃者が自分のパスキーを勝手に追加できない——という保護です。

**対処**: テストでも実際にパスワードを入力してログインします。
そして「再認証を求めること」自体もテストにします。

### 8.3 レート制限がテスト間で持ち越されて 429 になる

**原因**: allauth のログイン試行回数はキャッシュに記録されます。
Django のテストはデータベースをロールバックしますが、**キャッシュは戻しません**。

6日目の「サイトマップがテスト間で汚染される」問題と、原因はまったく同じです。

**対処**: `setUp()` で `cache.clear()` します。

### 8.4 編集者が他人の記事を編集できない（スクリーンショットで発見）

```text
403 Forbidden
```

**テストは 252 件すべて通っていました。**

**原因**: `_can_edit()` が `is_staff` しか見ていませんでした。
`is_staff` は「Django の管理画面へ入れる」という意味であって、
「編集者である」という意味ではありません。**この2つを混同していました。**

**なぜテストで気づけなかったか**: テストの `create_staff()` は
`is_staff=True` を付けていましたが、
**実運用の「編集者」グループには is_staff が付いていません**。
テスト用のユーザーと、実際に配る役割が食い違っていました。

**対処**: 「4.4」を参照してください。
自動保存 API も、独自の条件を書かずに同じ関数を使うよう直しました。

### 8.5 多要素認証を必須にしたら、既存のテストが3件落ちた

```text
FAIL: test_staff_can_preview_any_draft      AssertionError: 302 != 200
FAIL: test_staff_can_edit_others_article    AssertionError: 302 != 200
FAIL: test_staff_can_autosave_others_article AssertionError: 302 != 200
```

5日目にも同じことが起きています（公開権限を分離したら3日目のテストが落ちた）。
**バグではなく仕様変更** なので、直すのはテストの側です。

**対処**: テスト用のスタッフも、実運用と同じ「多要素認証を登録済み」にそろえます。

### 8.6 ログイン済みでログイン画面を撮ると、ダッシュボードが撮れる

スクリーンショットの4枚が **バイト単位で同一** になりました。

**原因**: 撮影スクリプトが最初にログインしてから全ページを回っていたため、
`/accounts/login/` が `/dashboard/` へリダイレクトされていました。

**対処**: 匿名用と認証済み用でブラウザーのコンテキストを分け、
撮影後にハッシュで重複を検出します。

---

## 9. 動作確認

### TOTP

- [ ] `/accounts/2fa/` が開く
- [ ] `/accounts/2fa/totp/activate/` で QR コードが出る
- [ ] `force_login` 相当（セッションだけ）では、設定画面が再認証へ飛ぶ
- [ ] 認証アプリで読み取り、表示されたコードで有効化できる
- [ ] ログアウトして再ログインすると、パスワードの後にコードを求められる
- [ ] 間違ったコードではログインが完了しない

### リカバリコード

- [ ] 10個のコードが発行される
- [ ] 一度表示したあと、同じ画面をもう一度開いても見られない
- [ ] リカバリコードでログインできる
- [ ] **同じコードは2回使えない**

### パスキー

- [ ] `/accounts/2fa/webauthn/` が 500 にならない（`humanize` が入っている）
- [ ] パスキーを登録できる（HTTPS か localhost が必要）
- [ ] パスキーだけでログインできる
- [ ] 複数のパスキーを登録できる
- [ ] パスキーを削除するとき、再認証を求められる

### 管理者の必須化

- [ ] `is_staff` のユーザーで、MFA 未登録だと `/accounts/2fa/` へ飛ばされる
- [ ] その状態でも設定画面・ログアウト・静的ファイルには到達できる
- [ ] リカバリコードだけ登録した状態では、まだ飛ばされる
- [ ] TOTP を登録すると、通常のページへ進める
- [ ] `is_staff` でないユーザーは影響を受けない

### システムチェック

```bash
DJANGO_DEBUG=0 DJANGO_SECRET_KEY=dummy python manage.py check --deploy
```

- [ ] `MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN=1` のとき `accounts.E001` が出る
- [ ] `EMAIL_BACKEND` がコンソールのとき `accounts.E002` が出る
- [ ] `DEBUG=True` のときは何も出ない

---

## 10. セキュリティ上の注意

### 復旧手段を必ず用意する

認証を強くするほど、失ったときに戻れなくなります。

```text
【パスキーだけの構成】
スマートフォンを水没させる
   ↓ パスキーは端末から出ない設計なので、他の端末では使えない
   ↓ 二度とログインできない
```

この CMS では3層にしています。

1. 日常: パスキー / TOTP
2. 控え: リカバリコード（紙）
3. 最終: 管理者による本人確認

`MFA_PASSKEY_SIGNUP_ENABLED = False` にしているのも同じ理由です。
最初からパスキーだけでアカウントを作らせると、控えを取る機会がありません。

### リカバリコードは一度だけ表示する

```python
MFA_RECOVERY_CODES_SHOW_ONCE = True
```

いつでも見られる状態は、
「セッションを盗まれた時点で全コードが漏れる」ことを意味します。

### 認証手段の追加・削除に再認証を要求する

```python
ACCOUNT_REAUTHENTICATION_REQUIRED = True
```

これが無いと、こうなります。

```text
攻撃者がセッションを盗む
   ↓ パスキー追加画面を開く
   ↓ 自分の端末のパスキーを登録
   ↓ 以後、正規のログイン手段として使える（セッションが切れても入れる）
```

**一時的な侵入が、永続的な侵入に変わります。**

### 迂回路を1本ずつ潰す

多要素認証を有効にしただけでは終わりません。
**「有効にした認証を通らずに入れる道が残っていないか」**を別に確かめます。

この CMS で見つかった2本は、どちらも「入口が2つあった」という形でした。

| 迂回路 | 塞ぎ方 |
| --- | --- |
| `/admin/login/`（allauth を通らない） | allauth のログインへリダイレクト（4.3） |
| パスキー単独ログイン（2段目が飛ぶ） | セッションの成立要素で判定（4.2） |

確かめ方は「入れないはずの手順で、実際に入ってみる」ことです。
設定値を読むテストではなく、**攻撃の手順をそのままテストに書きます**。

```python
def test_password_alone_cannot_enter_the_admin(self):
    add_totp(self.staff)
    self.client.post(self.admin_login_url, {...})     # パスワードだけ送る
    response = self.client.get(f"/{settings.ADMIN_URL_PATH}/")
    self.assertEqual(response.status_code, 302)       # 入れないこと
```

修正前のコードに対して、このテストは `AssertionError: 200 != 302` で落ちます。
**200 が返っていた**という事実が、そのまま不具合の証拠になります。

### テストの近道が、穴を隠すことがある

`Client.login()` と `Client.force_login()` は
`django.contrib.auth.login()` を直接呼びます。速くて便利ですが、
**allauth のログインフローを通りません**。

セッションに認証記録が残らないので、
「セッションが何で成立したか」を見る判定は、記録が空のまま通されます。
この CMS では、記録が空のセッションも**通さない**ことにしました。

```python
def test_session_without_any_allauth_record_is_blocked(self):
    """allauth を通っていないセッションは通さない（安全側に倒す）。"""
```

締め出しても、ログインし直せば戻れます。
緩めておくと、将来 `auth.login()` を直接呼ぶコードが増えたときに
黙って素通りします。**戻れる不便は、気づけない穴より安い**という判断です。

代わりに、テスト側を本番と同じ経路に寄せました。

```python
# blog/tests/factories.py
def login_staff(client, user, password=...):
    """スタッフとして、本番と同じ経路でログインする。"""
```

これを入れた時点で、既存のテストが3件落ちました。
`create_staff()` が確認済みのメールアドレスを作っていなかったためです
（`ACCOUNT_EMAIL_VERIFICATION = "mandatory"` なのに）。
近道で入っている間は、その不足に気づけませんでした。

### WebAuthn は HTTPS が前提

```python
MFA_WEBAUTHN_ALLOW_INSECURE_ORIGIN = ...
```

ブラウザーは、HTTPS（と localhost）以外で WebAuthn API を提供しません。
この設定を本番で有効にすると、その保護が外れます。

**「テストで確かめる」だけでは不十分です。**
テストは実行しなければ動きません。
システムチェックなら、`migrate` のたびに必ず走ります。

### 管理者だけを必須化の対象にする

```python
MFA_REQUIRED_FOR_STAFF = True
```

全員に強制すると、次のことが起きます。

- 記事を1本書くだけの人が設定でつまずく
- 「面倒だから」と共有アカウントが生まれる
- サポート対応の負荷が増える

**被害が大きい権限を持つ人から順に必須化する**のが現実的です。

### スクリーンショットに秘密を写さない

この記事の TOTP 設定画面には、シークレットと QR コードが写っています。
手元の SQLite にしか存在しないデモ用アカウントのものです。

**本番の画面をそのまま記事へ載せないでください。**
QR コードは、それだけで認証アプリに登録できてしまいます。

---

## 11. 今日の復習問題

**問1.** TOTP は、サーバーと端末がどうやって同じコードを出しますか。
通信していないのはなぜ問題にならないのですか。

**問2.** パスキー（WebAuthn）がフィッシングに強い理由を説明してください。
パスワードや TOTP が弱い理由も答えてください。

**問3.** リカバリコードだけを登録した状態を「多要素認証を設定済み」と
みなしてはいけないのはなぜですか。

**問4.** 管理者への多要素認証を必須にするミドルウェアで、
通しておかなければならない URL を4種類挙げてください。

**問5.** 危険な設定の検出を、テストではなくシステムチェックに置く理由は何ですか。

**問6.** パスキー1本でのログインを、この CMS が「1要素」として数えるのはなぜですか。
「登録時に生体認証を要求しているから2要素だ」という説明の、どこが誤りですか。

**問7.** 多要素認証を有効にしたのに、`/admin/login/` を残しておくと
どうなりますか。なぜ画面を見ているだけでは気づけないのですか。

<details>
<summary>解答</summary>

**問1.**
登録時に共有した秘密鍵と、現在時刻（30秒単位のカウンター）から、
両者が独立に HMAC-SHA1 を計算して同じ6桁を得ます。
計算に必要なのは秘密鍵と時刻だけなので、通信は不要です。
そのため機内モードでも動きます。

**問2.**
WebAuthn の署名には、アクセスしているドメイン名が含まれます。
偽サイトで署名を作らせても、その署名は偽サイト向けなので
本物のサーバーでは検証に失敗します。
パスワードや TOTP のコードは、偽サイトへ入力された値を
そのまま本物のサーバーへ中継できてしまいます。

ただし「フィッシングに強い」ことと「1本で2要素ぶん」は別です。
後者は UV（生体認証・PIN の確認）が行われて初めて言えることで、
認証時に `user_verification=required` を指定しなければ保証されません。

**問3.**
リカバリコードは「他の手段を失ったときの控え」であり、
日常的に使う認証手段ではないためです。
これだけを認めると、利用者は10枚の紙を毎回持ち歩くことになります。

**問4.**
多要素認証の設定画面、ログアウト、再認証の画面、静的ファイルの4種類です。
設定画面を塞ぐと無限リダイレクトになり、
ログアウトを塞ぐと抜け出せず、
再認証を塞ぐと設定画面の手前で止まり、
静的ファイルを塞ぐと CSS が当たらず画面が崩れます。

**問5.**
テストは開発者が実行したときだけ動きますが、
システムチェックは `runserver` `migrate` `check --deploy` のたびに必ず走ります。
「本番で危険な設定になっていないか」は、
実行し忘れようがない場所に置くべきです。

**問6.**
認証時に UV が行われる保証が無いためです。
allauth は登録時に `user_verification=REQUIRED` を指定しますが、
**認証時は `PREFERRED`** を使います。
そして fido2 は `REQUIRED` のときしか UV フラグを検査しません。

「登録時に要求した」は、その1回について言えることでしかありません。
毎回の認証で確認されるかどうかは、認証時の指定で決まります。
PIN を設定していないセキュリティキーなら、拾って挿すだけで通ります。

**問7.**
`admin.site.urls` に含まれる admin 自身のログイン画面は、
allauth のログインフローを通らないため、2段目の認証が実行されません。
TOTP もパスキーも登録済みの管理者が、パスワードだけで管理画面へ入れます。

画面で気づけないのは、**正しい手順で操作している限り、この経路を通らない**からです。
`/accounts/login/` からログインすれば、ちゃんと TOTP を求められます。
迂回路は「わざと変な入り方を試す」までは姿を現しません。
だからテストに、正規でない手順のほうを書きます。

</details>

---

## 12. Git の差分

```text
タグ    : day-09
コミット: day-09: TOTP・リカバリコード・パスキー(WebAuthn)を実装
```

```bash
git diff day-08 day-09
```

必須化ミドルウェアとシステムチェックだけを見る場合はこちらです。

```bash
git show day-09 -- accounts/middleware.py accounts/checks.py
```

テストは 252 件になりました。

```bash
python manage.py test
```

---

## 13. 次回予告

10日目は、本番へ出せる状態に仕上げます。

- PostgreSQL への移行
- Redis（レート制限を共有キャッシュにする）
- Docker Compose
- 設定ファイルの分割（`base` / `local` / `production`）
- `SECURE_*` と HSTS
- バックアップと復元テスト

そのあと第2部として、**Linux・Nginx・秘密鍵・Let's Encrypt** の
デプロイ編（10日間）へ続きます。

次回 → 【10日目】Django CMS 完成――テスト・Docker・セキュリティ・本番公開
