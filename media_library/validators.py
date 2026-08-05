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

from __future__ import annotations

import io

from django.core.exceptions import ValidationError
from django.utils.deconstruct import deconstructible

# 許可する画像形式（Pillow が返す format 名）。
ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}

# 許可する拡張子。実体の検証と二重にかける。
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}

# 1ファイルあたりの上限（バイト）。
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5 MiB

# 画像の最大画素数。極端に大きい画像は伸長時にメモリを食い尽くす
# （いわゆる decompression bomb）。
#
# ★この値を Image.MAX_IMAGE_PIXELS へ代入してはいけない★
#
# Image.MAX_IMAGE_PIXELS は Pillow というモジュール全体で共有される
# ただの変数で、プロセス内の全リクエストから見える。
# 「検証の間だけ書き換えて finally で戻す」という書き方は、
# Gunicorn のスレッドワーカーのように**同じプロセスで複数の
# リクエストを同時に処理する構成では成立しない**。
#
#   リクエストA: 50_000_000 に下げる
#   リクエストB: 画像を開く          ← A の設定が効いてしまう
#   リクエストA: 元の値に戻す
#   リクエストB: 画像を開く          ← 今度は既定値に戻っている
#
# B の結果が A の進み具合で変わる。落ちるときと落ちないときがある、
# という最も再現しにくい形の不具合になる。
#
# ここでは Pillow の既定値には触らず、開いた後に自分で
# 幅×高さを検査する。共有されている値を書き換えないので、
# 同時に何本走っていても結果が変わらない。
MAX_IMAGE_PIXELS = 50_000_000  # 50 メガピクセル


class UploadValidationError(ValidationError):
    """アップロード検証に失敗したことを表す。"""


@deconstructible
class ImageUploadValidator:
    """ImageField / FileField へ渡す検証クラス。

    ``@deconstructible`` を付けると、マイグレーションファイルへ
    シリアライズできるようになる（付け忘れると makemigrations が失敗する）。
    """

    def __init__(self, max_size: int = MAX_UPLOAD_SIZE):
        self.max_size = max_size

    def __eq__(self, other):
        return isinstance(other, ImageUploadValidator) and self.max_size == other.max_size

    def __call__(self, uploaded_file):
        validate_image_upload(uploaded_file, max_size=self.max_size)


def validate_image_upload(uploaded_file, *, max_size: int = MAX_UPLOAD_SIZE) -> str:
    """アップロードファイルを検証し、Pillow が判定した形式名を返す。

    Raises:
        UploadValidationError: 検証に失敗した場合。
    """
    # --- 1. サイズ -------------------------------------------------------
    size = getattr(uploaded_file, "size", None)
    if size is None:
        raise UploadValidationError("ファイルサイズを取得できませんでした。")
    if size == 0:
        raise UploadValidationError("空のファイルはアップロードできません。")
    if size > max_size:
        limit_mb = max_size / (1024 * 1024)
        raise UploadValidationError(
            f"ファイルサイズが大きすぎます（上限 {limit_mb:.0f} MB）。"
        )

    # --- 2. 拡張子 -------------------------------------------------------
    name = (getattr(uploaded_file, "name", "") or "").lower()
    if "." not in name:
        raise UploadValidationError("拡張子のないファイルは受け付けません。")
    extension = name[name.rfind(".") :]
    if extension not in ALLOWED_EXTENSIONS:
        allowed = "、".join(sorted(ALLOWED_EXTENSIONS))
        raise UploadValidationError(
            f"この拡張子は許可されていません（許可: {allowed}）。"
        )

    # --- 3. 実体 ---------------------------------------------------------
    # Pillow をここで import する。モジュール読み込み時ではなく
    # 呼ばれたときに読むことで、Pillow 未導入でも他機能が動く。
    from PIL import Image, UnidentifiedImageError

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
            # Image.open() はヘッダーしか読まないので、この時点では
            # 画素データを伸長していない。宣言された寸法は分かる。
            with Image.open(io.BytesIO(payload)) as image:
                image_format = image.format
                width, height = image.size

            # ★伸長する前に画素数を検査する★
            # ここを verify() より後ろに置くと、検査する前に
            # 巨大な画像を読ませることになり、対策の意味が無くなる。
            if width * height > MAX_IMAGE_PIXELS:
                raise UploadValidationError(
                    f"画像の画素数が大きすぎます"
                    f"（{width}×{height}、上限 {MAX_IMAGE_PIXELS:,} 画素）。"
                )

            # 壊れたファイルの検出。verify() の後は画像を読み直す必要が
            # あるので、開き直している。
            with Image.open(io.BytesIO(payload)) as image:
                image.verify()
        except UnidentifiedImageError as exc:
            raise UploadValidationError(
                "画像として読み取れませんでした。拡張子だけを変えたファイルの可能性があります。"
            ) from exc
        except Image.DecompressionBombError as exc:
            # Pillow 自身の既定値（この検証より緩い）に引っかかった場合。
            # 上の自前検査で先に落ちるはずだが、Pillow 側の判定が
            # 変わっても取りこぼさないよう残しておく。
            raise UploadValidationError("画像の画素数が大きすぎます。") from exc
        except OSError as exc:
            raise UploadValidationError("壊れた画像ファイルです。") from exc
    finally:
        # 後続の保存処理のために先頭へ巻き戻す。
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)

    if image_format not in ALLOWED_IMAGE_FORMATS:
        allowed = "、".join(sorted(ALLOWED_IMAGE_FORMATS))
        raise UploadValidationError(
            f"この画像形式は許可されていません（許可: {allowed}）。"
        )

    # 拡張子と実体が食い違うファイルを拒否する。
    expected = {
        ".jpg": "JPEG",
        ".jpeg": "JPEG",
        ".png": "PNG",
        ".gif": "GIF",
        ".webp": "WEBP",
    }[extension]
    if expected != image_format:
        raise UploadValidationError(
            f"拡張子（{extension}）と実際の形式（{image_format}）が一致しません。"
        )

    if width <= 0 or height <= 0:
        raise UploadValidationError("画像の寸法を取得できませんでした。")

    return image_format


def _read_all(uploaded_file) -> bytes:
    """アップロードファイルの内容を最初から最後まで読み取る。"""
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    if hasattr(uploaded_file, "chunks"):
        payload = b"".join(uploaded_file.chunks())
    else:
        payload = uploaded_file.read()
    if hasattr(uploaded_file, "seek"):
        uploaded_file.seek(0)
    return payload
