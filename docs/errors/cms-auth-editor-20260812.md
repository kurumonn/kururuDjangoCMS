# DEVQA-CMS-20260812: CMS 認証・編集導線の再確認

## 対象

- アプリ: `accounts`, `dashboard`, `blog`
- 対象: 管理者セッション要件、記事編集フォーム、autosave API
- 追跡ブランチ: `codex/cms-assurance-20260812`

## 現象

1. allauth の mandatory メール確認設定と必須メールフィールド設定が不整合で、
   management command の system check が起動前に停止していた。
2. パスキーが登録済みというだけで、パスワードだけの管理者セッションが管理画面へ
   到達できる余地があった。過去の認証記録も期限なしで参照されていた。
3. autosave に不正な `Content-Length` が届くと `int()` 例外で 500 になり得た。
4. CSRF 失敗やセッション切れで HTML が返ると、ブラウザー側が JSON parse 例外を
   通信障害として表示していた。

## 受入条件

- `ACCOUNT_EMAIL_VERIFICATION="mandatory"` と
  `ACCOUNT_EMAIL_REQUIRED=True` が同時に満たされ、system check が起動する。
- 管理者は現在のセッションに新しい TOTP、またはパスキーと独立した追加要素の
  両方が記録されない限り管理画面へ入れない。
- 認証記録は `ACCOUNT_REAUTHENTICATION_TIMEOUT` 内だけ有効で、期限切れ・未来時刻・
  不正な時刻は無効として扱う。
- autosave の不正な `Content-Length` は JSON 400、CSRF/セッション切れの非 JSON 403 は
  再読み込みを促すメッセージとして扱い、500 を返さない。
- 編集フォームの GET、記事本文/ブロック/履歴の POST、未ログイン autosave の統合テストがある。

## 検証

- 重点テスト: 7件 OK
- 管理者セッション要件: 14件 OK
- `node --check static/js/block-editor.js`: OK
- `git diff --check`: OK
- 既存編集/autosave 21件は 20件 OK、1件は現行システム Python の依存が
  リポジトリ指定（Django 5.2.17 / allauth 65.18.0）ではなく、旧版
  （Django 4.2.16 / allauth 65.3.0）のログイン補助フローに依存する既存テスト失敗。
  固定依存の CI またはコンテナで再実行する。

## 未実施

- 本番デプロイ、メール送信、物理認証器による WebAuthn E2E はこのチケットの対象外。
- GitHub 上の `noujyuku_bbs` は使用しない。
