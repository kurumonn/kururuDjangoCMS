# KururuCMS外部プラグイン

## 安全境界

KururuCMS本体が提供するのはcms_plugins API v1、許可リスト型の検出、
有効化DB、URLと記事ブロックの登録機構だけです。プラグイン実装は別リポジトリの
Python wheelにします。

管理画面はデプロイ済みのPluginActivation行を有効、無効にできますが、
パッケージ名、GitHub URL、Pythonコードを入力してインストールすることはできません。
Pythonコードの導入は、レビュー、テスト、wheelのSHA-256固定を終えた
デプロイ工程でだけ行います。

## プラグイン側の最小契約

pyproject.tomlでkururucms.plugins entry pointを公開します。

    [project.entry-points."kururucms.plugins"]
    my_plugin = "my_plugin.apps.MyPluginConfig"

AppConfig.ready()でPluginDefinitionを一度登録します。

    from cms_plugins.registry import PluginDefinition, register_plugin

    register_plugin(PluginDefinition(
        key="my_plugin",
        name="My Plugin",
        version="1.0.0",
        description="説明",
        urlconf="my_plugin.urls",
        url_prefix="my-plugin",
    ))

記事ブロックを追加する場合はPluginBlockとEditorFieldを定義します。
検証関数はブラウザから来たdataを信用せず、正規化済みdictを返します。
描画はプラグインのDjangoテンプレートを使い、任意HTML文字列を返しません。

CMSホストが登録URLを一括してPluginActivationでガードします。プラグイン側でも
require_plugin_enabledを付けると、URLconfを単独利用した場合の多層防御になります。
DBで無効化した直後からURLは404になり、記事内のプラグインブロックも描画されません。

## 再現可能な導入

プラグインリポジトリでwheelを作成し、pip hashでSHA-256を取得します。

    python -m build --wheel
    python -m pip hash dist\my_plugin-1.0.0-py3-none-any.whl

wheelをCMS作業コピーのplugin_wheels/へ置きます。このディレクトリのwheelは
gitignoreされます。plugin-requirements.lockへ版とhashを記録します。
プラグインがCMS本体にない追加依存を使う場合、その推移的依存もすべて版とhash付きの
独立行として同じlockへ記録します。

    my-plugin==1.0.0 --hash=sha256:<取得した値>

Dockerfileはcore依存と外部plugin依存を別のlockとして検証し、ネットワークを
使わずに最終イメージへインストールします。実行中コンテナでpipは実行しません。

環境変数KURURU_PLUGIN_PACKAGESへentry point名をカンマ区切りで指定し、
イメージをビルドします。

    KURURU_PLUGIN_PACKAGES=my_plugin

次にmigrate、check --deploy、collectstaticを行い、管理画面の
「CMSプラグイン」で有効化します。許可リストにあるwheelが無い場合は起動時に失敗し、
依存関係に偶然含まれた未知entry pointは読み込みません。

## 互換性変更

CMS_PLUGIN_API_VERSIONを変更するときは、旧APIを直ちに削除せず移行期間を設けます。
互換でないapi_versionを登録しようとしたプラグインは起動時に拒否されます。
