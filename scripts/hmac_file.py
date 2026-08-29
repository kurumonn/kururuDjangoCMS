"""秘密値をプロセス引数へ出さず、ファイルのHMAC-SHA-256を計算する。"""

from __future__ import annotations

import argparse
import hmac
from hashlib import sha256
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("key_file")
    parser.add_argument("input_file")
    args = parser.parse_args()

    key = Path(args.key_file).read_bytes()
    digest = hmac.new(key, digestmod=sha256)
    with Path(args.input_file).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    print(digest.hexdigest())


if __name__ == "__main__":
    main()
