from __future__ import annotations

import argparse
import re
import shutil
import signal
import sys
from pathlib import Path

from photostow.audit import (
    delete_duplicate_groups,
    parse_duplicate_group_file,
    remote_duplicate_groups,
    write_audit,
    write_duplicate_groups,
)
from photostow.core import hash_tree, missing_hash_records, parse_sha_lines
from photostow.photos import (
    earliest_created,
    iter_assets,
    library_destinations,
    library_hashes,
    missing_library_assets,
)
from photostow.remote import (
    copy_paths_tar,
    copy_records_by_year,
    copy_stage,
    install_remote_ledger,
    missing_records,
    paths_from_missing_tsv,
    prune_remote_ledger,
    relative_paths,
    stage_by_year,
    update_remote_ledger,
    validate_source_paths,
)
from photostow.transfer import iter_cached


def cmd_hash(args: argparse.Namespace) -> int:
    root = Path(args.root)
    for digest, path in hash_tree(root):
        print(f"{digest}  {path}")
    return 0


# Build a resumable, duplicate-aware plan from the local hash cache.
def cmd_transfer_plan(args: argparse.Namespace) -> int:
    oxygen_digests = set()
    with open(args.oxygen_inventory, encoding="utf-8") as stream:
        for line in stream:
            digest = line.strip()
            if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"invalid Oxygen digest: {digest}")
            if digest:
                oxygen_digests.add(digest)
    destinations = (
        library_destinations(Path(args.photos_library)) if args.photos_library else None
    )
    duplicates: dict[str, list[Path]] = {}
    destination_digests: dict[Path, str] = {}
    seen: set[str] = set()
    queued = 0
    collisions = 0
    with Path(args.output).open("w", encoding="utf-8") as stream:
        stream.write("sha256\tpath" + ("\tdestination" if destinations else "") + "\n")
        for record in iter_cached(Path(args.source_root), Path(args.cache)):
            paths = duplicates.setdefault(record.digest, [])
            paths.append(record.path)
            destination = destinations.get(record.path) if destinations else None
            if destinations and destination is None:
                raise ValueError(f"file is not a Photos asset: {record.path}")
            if destination is not None:
                prior = destination_digests.setdefault(destination, record.digest)
                if prior != record.digest:
                    print(
                        f"destination collision {destination}: {prior} vs {record.digest}",
                        file=sys.stderr,
                    )
                    collisions += 1
                    continue
            if record.digest not in oxygen_digests and record.digest not in seen:
                line = f"{record.digest}\t{record.path}"
                if destination is not None:
                    line += f"\t{destination}"
                stream.write(line + "\n")
                seen.add(record.digest)
                queued += 1
    for digest, paths in sorted(duplicates.items()):
        if len(paths) > 1:
            print(f"duplicate {digest}: {' | '.join(map(str, paths))}", file=sys.stderr)
    duplicate_count = sum(len(paths) > 1 for paths in duplicates.values())
    print(f"queued {queued} files", file=sys.stderr)
    return 1 if duplicate_count or collisions else 0


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
        print(
            f"earliest missing creation date: {earliest.isoformat()}", file=sys.stderr
        )
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
        source_root = Path(args.source_root)
        rels = relative_paths(paths, source_root)
        validate_source_paths(rels, source_root)
        if args.dry_run:
            for rel in rels:
                print(rel)
            return 0
        count = copy_paths_tar(rels, Path(args.source_root), args.host, args.dest_root)
    print(f"copied {count} files", file=sys.stderr)
    return 0


def cmd_stage_review(args: argparse.Namespace) -> int:
    review_dir = Path(args.review_dir)
    if review_dir.exists() and any(review_dir.iterdir()):
        if not args.replace:
            raise SystemExit(f"{review_dir} is not empty; use --replace to rebuild it")
        shutil.rmtree(review_dir)
    records = missing_records(Path(args.missing_tsv))
    count = stage_by_year(records, Path(args.source_root), review_dir)
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


def cmd_duplicate_groups(args: argparse.Namespace) -> int:
    groups = remote_duplicate_groups(args.host, args.root, Path(args.ledger))
    if args.output:
        write_duplicate_groups(groups, Path(args.output))
    else:
        write_duplicate_groups(groups, Path("/dev/stdout"))
    print(f"duplicate groups: {len(groups)}", file=sys.stderr)
    return 0


def cmd_delete_duplicates(args: argparse.Namespace) -> int:
    groups = parse_duplicate_group_file(Path(args.duplicate_groups))
    count = delete_duplicate_groups(args.host, groups, dry_run=not args.yes)
    action = "would delete" if not args.yes else "deleted"
    print(f"{action} {count} duplicate files", file=sys.stderr)
    return 0


def cmd_audit_new_remote(args: argparse.Namespace) -> int:
    print(
        write_audit(
            args.host,
            args.root,
            Path(args.ledger),
            Path(args.workdir),
            do_hash=args.hash,
        )
    )
    return 0


def cmd_install_remote_ledger(args: argparse.Namespace) -> int:
    install_remote_ledger(
        args.host, Path(args.local_ledger), args.remote_ledger, args.keep
    )
    print(
        f"installed {args.local_ledger} to {args.host}:{args.remote_ledger}",
        file=sys.stderr,
    )
    return 0


def cmd_prune_remote_ledger(args: argparse.Namespace) -> int:
    before, after = prune_remote_ledger(
        args.host,
        args.root,
        Path(args.ledger),
        Path(args.output) if args.output else None,
    )
    print(f"pruned ledger rows {before} -> {after}", file=sys.stderr)
    return 0


def cmd_update_remote_ledger(args: argparse.Namespace) -> int:
    count = update_remote_ledger(
        args.host,
        args.root,
        Path(args.ledger),
        Path(args.output) if args.output else None,
    )
    print(f"hashed {count} new remote files", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photostow")
    sub = parser.add_subparsers(required=True)

    hash_parser = sub.add_parser("hash", help="hash all files under a directory")
    hash_parser.add_argument("root")
    hash_parser.set_defaults(func=cmd_hash)

    transfer_parser = sub.add_parser(
        "transfer-plan", help="plan one transfer per missing content digest"
    )
    transfer_parser.add_argument("source_root")
    transfer_parser.add_argument("oxygen_inventory")
    transfer_parser.add_argument("cache")
    transfer_parser.add_argument("output")
    transfer_parser.add_argument(
        "--photos-library",
        help="derive YYYY/filename destinations from a Photos library",
    )
    transfer_parser.set_defaults(func=cmd_transfer_plan)

    missing_parser = sub.add_parser(
        "missing", help="list source hashes absent from archive"
    )
    missing_parser.add_argument("source_hashes")
    missing_parser.add_argument("archive_hashes")
    missing_parser.set_defaults(func=cmd_missing)

    inspect_parser = sub.add_parser(
        "inspect-library", help="list Photos library assets"
    )
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

    prune_parser = sub.add_parser(
        "prune-remote-ledger",
        help="remove ledger rows for remote paths that no longer exist",
    )
    prune_parser.add_argument("host")
    prune_parser.add_argument("root")
    prune_parser.add_argument("ledger")
    prune_parser.add_argument("--output")
    prune_parser.set_defaults(func=cmd_prune_remote_ledger)

    install_parser = sub.add_parser(
        "install-remote-ledger", help="rotate and copy local ledger to remote"
    )
    install_parser.add_argument("host")
    install_parser.add_argument("local_ledger")
    install_parser.add_argument("remote_ledger")
    install_parser.add_argument("--keep", type=int, default=5)
    install_parser.set_defaults(func=cmd_install_remote_ledger)

    audit_parser = sub.add_parser(
        "audit-new-remote", help="estimate/hash only remote paths missing from ledger"
    )
    audit_parser.add_argument("host")
    audit_parser.add_argument("root")
    audit_parser.add_argument("ledger")
    audit_parser.add_argument("--workdir", default="audit-new-remote")
    audit_parser.add_argument("--hash", action="store_true")
    audit_parser.set_defaults(func=cmd_audit_new_remote)

    dup_parser = sub.add_parser(
        "duplicate-groups",
        help="report current remote duplicate groups from ledger hashes",
    )
    dup_parser.add_argument("host")
    dup_parser.add_argument("root")
    dup_parser.add_argument("ledger")
    dup_parser.add_argument("--output")
    dup_parser.set_defaults(func=cmd_duplicate_groups)

    delete_dup_parser = sub.add_parser(
        "delete-duplicates", help="delete all but first path in each duplicate group"
    )
    delete_dup_parser.add_argument("host")
    delete_dup_parser.add_argument("duplicate_groups")
    delete_dup_parser.add_argument("--yes", action="store_true")
    delete_dup_parser.set_defaults(func=cmd_delete_duplicates)

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
    review_parser.add_argument("--replace", action="store_true")
    review_parser.set_defaults(func=cmd_stage_review)

    copy_tree_parser = sub.add_parser(
        "copy-tree",
        help="copy a reviewed tree only; prefer make archive-reviewed",
    )
    copy_tree_parser.add_argument("review_dir")
    copy_tree_parser.add_argument("host")
    copy_tree_parser.add_argument("dest_root")
    copy_tree_parser.add_argument("--dry-run", action="store_true")
    copy_tree_parser.set_defaults(func=cmd_copy_tree)

    return parser


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError) as error:
        print(f"photostow failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
