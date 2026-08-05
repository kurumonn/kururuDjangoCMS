"""4日目: アップロード検証のテスト。

ここは CMS で最も攻撃されやすい入口なので、
「通ってはいけないもの」を1件ずつ固定する。
"""

from __future__ import annotations

import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from PIL import Image

from .validators import (
    MAX_IMAGE_PIXELS,
    MAX_UPLOAD_SIZE,
    UploadValidationError,
    validate_image_upload,
)


def make_image_bytes(fmt: str = "PNG", size=(40, 30), color="red") -> bytes:
    """テスト用の画像をメモリ上で作る。"""
    buffer = io.BytesIO()
    mode = "RGB"
    image = Image.new(mode, size, color)
    image.save(buffer, format=fmt)
    return buffer.getvalue()


def upload(name: str, payload: bytes, content_type: str = "image/png"):
    return SimpleUploadedFile(name, payload, content_type=content_type)


class ValidImageTests(TestCase):
    def test_png_is_accepted(self):
        result = validate_image_upload(upload("photo.png", make_image_bytes("PNG")))
        self.assertEqual(result, "PNG")

    def test_jpeg_is_accepted(self):
        result = validate_image_upload(
            upload("photo.jpg", make_image_bytes("JPEG"), "image/jpeg")
        )
        self.assertEqual(result, "JPEG")

    def test_webp_is_accepted(self):
        result = validate_image_upload(
            upload("photo.webp", make_image_bytes("WEBP"), "image/webp")
        )
        self.assertEqual(result, "WEBP")

    def test_uppercase_extension_is_accepted(self):
        result = validate_image_upload(upload("PHOTO.PNG", make_image_bytes("PNG")))
        self.assertEqual(result, "PNG")

    def test_file_pointer_is_rewound_for_saving(self):
        """検証で読み切ったあと、保存処理が読めるよう先頭へ戻す。"""
        uploaded = upload("photo.png", make_image_bytes("PNG"))
        validate_image_upload(uploaded)
        self.assertEqual(uploaded.tell(), 0)
        self.assertTrue(uploaded.read())


class RejectedUploadTests(TestCase):
    def assert_rejected(self, uploaded, fragment: str = ""):
        with self.assertRaises(UploadValidationError) as ctx:
            validate_image_upload(uploaded)
        if fragment:
            self.assertIn(fragment, str(ctx.exception))

    def test_empty_file_is_rejected(self):
        self.assert_rejected(upload("empty.png", b""), "空のファイル")

    def test_oversized_file_is_rejected(self):
        payload = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_UPLOAD_SIZE + 1)
        self.assert_rejected(upload("big.png", payload), "大きすぎます")

    def test_file_without_extension_is_rejected(self):
        self.assert_rejected(upload("noextension", make_image_bytes("PNG")), "拡張子")

    def test_disallowed_extension_is_rejected(self):
        """.php を .png に見せかけたファイルではなく、そもそも .php を拒否する。"""
        self.assert_rejected(
            upload("shell.php", b"<?php system($_GET['c']); ?>", "image/png"),
            "許可されていません",
        )

    def test_svg_is_rejected(self):
        """SVG は中に JavaScript を書ける。同一オリジンで配信すると XSS になる。"""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assert_rejected(upload("evil.svg", svg, "image/svg+xml"))

    def test_svg_renamed_to_png_is_rejected(self):
        """拡張子を .png に変えても、実体が画像でないので通らない。"""
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        self.assert_rejected(upload("evil.png", svg, "image/png"))

    def test_script_renamed_to_png_is_rejected(self):
        """拡張子だけを変えたスクリプトは Pillow が開けない。"""
        self.assert_rejected(
            upload("payload.png", b"#!/bin/sh\nrm -rf /\n"),
            "画像として読み取れません",
        )

    def test_html_renamed_to_png_is_rejected(self):
        self.assert_rejected(upload("page.png", b"<html><body>hi</body></html>"))

    def test_extension_and_real_format_must_match(self):
        """中身は本物の PNG だが、拡張子が .jpg のファイル。"""
        self.assert_rejected(
            upload("mismatch.jpg", make_image_bytes("PNG"), "image/jpeg"),
            "一致しません",
        )

    def test_truncated_image_is_rejected(self):
        payload = make_image_bytes("PNG")
        self.assert_rejected(upload("broken.png", payload[: len(payload) // 2]))

    def test_content_type_header_is_not_trusted(self):
        """Content-Type は利用者が自由に付けられるので、判断材料にしない。

        ここでは「image/png と名乗るテキスト」を拒否できることを確認する。
        """
        self.assert_rejected(upload("lie.png", b"not an image at all", "image/png"))


class UploadPathTests(TestCase):
    def test_uploaded_filename_is_not_used_as_is(self):
        """利用者が付けたファイル名は保存パスに使わない。"""
        from .models import MediaAsset, upload_to

        asset = MediaAsset(created_at=None)
        path = upload_to(asset, "../../../etc/passwd.png")

        self.assertNotIn("..", path)
        self.assertNotIn("passwd", path)
        self.assertTrue(path.startswith("library/"))
        self.assertTrue(path.endswith(".png"))

    def test_two_uploads_of_same_name_get_different_paths(self):
        from .models import MediaAsset, upload_to

        asset = MediaAsset(created_at=None)
        first = upload_to(asset, "photo.png")
        second = upload_to(asset, "photo.png")
        self.assertNotEqual(first, second)


class DecompressionBombTests(TestCase):
    """画素数の検査が、共有状態を汚さずに動くこと。

    以前は検証の間だけ ``Image.MAX_IMAGE_PIXELS`` を書き換えて
    finally で戻していた。この変数は Pillow モジュール全体で共有され、
    プロセス内の全リクエストから見えるため、同じプロセスで
    複数のリクエストを同時に処理する構成では他方に影響する。
    """

    def test_global_pillow_limit_is_untouched_during_validation(self):
        """★検証の「最中」に共有値が変わっていないこと★

        前後で比べるだけでは足りない。以前の実装は finally で元へ戻して
        いたので、呼び出しの前後を比べる限り差は出なかった。
        他のリクエストから見えるのは**その途中**なので、
        途中を観測しなければ意味がない。
        """
        observed = []
        original_open = Image.open

        def spy(*args, **kwargs):
            observed.append(Image.MAX_IMAGE_PIXELS)
            return original_open(*args, **kwargs)

        with mock.patch.object(Image, "open", spy):
            validate_image_upload(upload("ok.png", make_image_bytes("PNG")))

        self.assertTrue(observed, "Image.open が呼ばれていない")
        for seen in observed:
            self.assertEqual(
                seen,
                Image.MAX_IMAGE_PIXELS,
                "検証中に Pillow の共有設定が書き換わっている。"
                "同じプロセスの他のリクエストに影響する。",
            )

    def test_oversized_image_is_rejected_by_our_own_check(self):
        """Pillow の既定より厳しい上限が、自前の検査で効くこと。

        Pillow の既定はおよそ 89,478,485 画素なので、この画像は
        Pillow 自身の判定には引っかからない。自前で数えていなければ通る。
        """
        pixels = MAX_IMAGE_PIXELS + 1
        width = 10_000
        height = pixels // width + 1
        self.assertLess(width * height, Image.MAX_IMAGE_PIXELS)

        payload = make_image_bytes("PNG", size=(width, height))

        with self.assertRaises(UploadValidationError) as caught:
            validate_image_upload(upload("huge.png", payload))

        self.assertIn("画素数", str(caught.exception))

    def test_image_just_under_the_limit_is_accepted(self):
        width, height = 10_000, MAX_IMAGE_PIXELS // 10_000
        self.assertLessEqual(width * height, MAX_IMAGE_PIXELS)

        payload = make_image_bytes("PNG", size=(width, height))

        self.assertEqual(validate_image_upload(upload("big.png", payload)), "PNG")
