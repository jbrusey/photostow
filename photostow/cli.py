from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photostow.core import hash_tree, missing_hash_records


def cmd_hash(args: argparse.Namespace) -> int:
    root = Path(args.root)
    for digest, path in hash_tree(root):
        print(f"{digest}  {path}")
    return 0


def cmd_missing(args: argparse.Namespace) -> int:
    with open(args.source_hashes, encoding="utf-8") as source, open(
        args.archive_hashes, encoding="utf-8"
    ) as archive:
        for digest, name in missing_hash_records(source, archive):
            print(f"{digest}  {name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photostow")
    sub = parser.add_subparsers(required=True)

    hash_parser = sub.add_parser("hash", help="hash all files under a directory")
    hash_parser.add_argument("root")
    hash_parser.set_defaults(func=cmd_hash)

    missing_parser = sub.add_parser("missing", help="list source hashes absent from archive")
    missing_parser.add_argument("source_hashes")
    missing_parser.add_argument("archive_hashes")
    missing_parser.set_defaults(func=cmd_missing)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
