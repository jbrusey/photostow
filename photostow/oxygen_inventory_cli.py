from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photostow.oxygen import object_digests


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-inventory")
    parser.add_argument("object_root", type=Path)
    args = parser.parse_args()
    try:
        for digest in object_digests(args.object_root):
            print(digest)
    except (OSError, ValueError) as error:
        print(f"oxygen-inventory failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
