from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from photostow.core import hash_tree, missing_hash_records, parse_sha_lines
from photostow.photos import (
    earliest_created,
    iter_assets,
    library_hashes,
    missing_library_assets,
)
from photostow.remote import (
    copy_paths_tar,
    copy_records_by_year,
    copy_stage,
    missing_records,
    paths_from_missing_tsv,
    relative_paths,
    stage_by_year,
    update_remote_ledger,
)


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


def cmd_inspect_library(args: argparse.Namespace) -> int:
    print("uuid\tcreated\tadjusted\tpresent\tpath")
    for asset in iter_assets(Path(args.library)):
        created = asset.created.isoformat() if asset.created else ""
        print(
            f"{asset.uuid}\t{created}\t{int(asset.has_adjustments)}\t"
            f"{int(asset.present)}\t{asset.path}"
        )
    return 0


def cmd_library_hashes(args: argparse.Namespace) -> int:
    for digest, asset in library_hashes(Path(args.library)):
        print(f"{digest}  {asset.path}")
    return 0


def cmd_library_missing(args: argparse.Namespace) -> int:
    with open(args.archive_hashes, encoding="utf-8") as archive:
        archived = {digest for digest, _ in parse_sha_lines(archive)}
    missing = missing_library_assets(Path(args.library), archived)
    print("sha256\tcreated\tadjusted\tpath")
    for digest, asset in missing:
        created = asset.created.isoformat() if asset.created else ""
        print(f"{digest}\t{created}\t{int(asset.has_adjustments)}\t{asset.path}")
    earliest = earliest_created([asset for _, asset in missing])
    if earliest:
        print(f"earliest missing creation date: {earliest.isoformat()}", file=sys.stderr)
    return 0


def cmd_copy_missing(args: argparse.Namespace) -> int:
    if args.by_year:
        records = missing_records(Path(args.missing_tsv))
        if args.dry_run:
            for record in records:
                print(f"{record.year}/{record.path.name}")
            return 0
        count = copy_records_by_year(
            records, Path(args.source_root), args.host, args.dest_root
        )
    else:
        paths = paths_from_missing_tsv(Path(args.missing_tsv))
        rels = relative_paths(paths, Path(args.source_root))
        if args.dry_run:
            for rel in rels:
                print(rel)
            return 0
        count = copy_paths_tar(rels, Path(args.source_root), args.host, args.dest_root)
    print(f"copied {count} files", file=sys.stderr)
    return 0


def cmd_stage_review(args: argparse.Namespace) -> int:
    records = missing_records(Path(args.missing_tsv))
    count = stage_by_year(records, Path(args.source_root), Path(args.review_dir))
    print(f"staged {count} files", file=sys.stderr)
    return 0


def cmd_copy_tree(args: argparse.Namespace) -> int:
    if args.dry_run:
        review_dir = Path(args.review_dir)
        for path in sorted(review_dir.rglob("*")):
            if path.is_file():
                print(path.relative_to(review_dir))
        return 0
    copy_stage(Path(args.review_dir), args.host, args.dest_root)
    print("copied reviewed tree", file=sys.stderr)
    return 0


def cmd_update_remote_ledger(args: argparse.Namespace) -> int:
    count = update_remote_ledger(
        args.host, args.root, Path(args.ledger), Path(args.output) if args.output else None
    )
    print(f"hashed {count} new remote files", file=sys.stderr)
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

    inspect_parser = sub.add_parser("inspect-library", help="list Photos library assets")
    inspect_parser.add_argument("library")
    inspect_parser.set_defaults(func=cmd_inspect_library)

    library_hash_parser = sub.add_parser(
        "library-hashes", help="hash present originals in a Photos library"
    )
    library_hash_parser.add_argument("library")
    library_hash_parser.set_defaults(func=cmd_library_hashes)

    library_missing_parser = sub.add_parser(
        "library-missing", help="list Photos originals absent from archive hashes"
    )
    library_missing_parser.add_argument("library")
    library_missing_parser.add_argument("archive_hashes")
    library_missing_parser.set_defaults(func=cmd_library_missing)

    update_parser = sub.add_parser(
        "update-remote-ledger", help="incrementally hash new files on a remote host"
    )
    update_parser.add_argument("host")
    update_parser.add_argument("root")
    update_parser.add_argument("ledger")
    update_parser.add_argument("--output")
    update_parser.set_defaults(func=cmd_update_remote_ledger)

    copy_parser = sub.add_parser("copy-missing", help="copy paths from missing TSV")
    copy_parser.add_argument("missing_tsv")
    copy_parser.add_argument("source_root")
    copy_parser.add_argument("host")
    copy_parser.add_argument("dest_root")
    copy_parser.add_argument("--by-year", action="store_true")
    copy_parser.add_argument("--dry-run", action="store_true")
    copy_parser.set_defaults(func=cmd_copy_missing)

    review_parser = sub.add_parser(
        "stage-review", help="hardlink missing files into a local review tree by year"
    )
    review_parser.add_argument("missing_tsv")
    review_parser.add_argument("source_root")
    review_parser.add_argument("review_dir")
    review_parser.set_defaults(func=cmd_stage_review)

    copy_tree_parser = sub.add_parser("copy-tree", help="copy a reviewed tree to remote")
    copy_tree_parser.add_argument("review_dir")
    copy_tree_parser.add_argument("host")
    copy_tree_parser.add_argument("dest_root")
    copy_tree_parser.add_argument("--dry-run", action="store_true")
    copy_tree_parser.set_defaults(func=cmd_copy_tree)

    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
