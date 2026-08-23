from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from photostow.oxygen import migrate

DEFAULT_ROOT = Path("/var/services/photo")


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-migrate")
    parser.add_argument("target", nargs="?")
    parser.add_argument("--root", dest="root_override", type=Path)
    parser.add_argument("--apply", nargs="?", const=True, default=False)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--path", type=Path, help="path or subtree beneath root")
    parser.add_argument("--limit", type=int, help="process at most N files")
    parser.add_argument("--object-root", type=Path, help="content object directory")
    parser.add_argument(
        "--manifest", type=Path, help="write reviewed dry-run selection"
    )
    parser.add_argument("--nice", type=int, default=10, help="add CPU niceness")
    parser.add_argument(
        "--jobs", type=int, default=1, help="hash workers (only 1 is supported)"
    )
    parser.add_argument("--exclude", action="append", default=[])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    apply_manifest = (
        Path(args.apply).parent.resolve() / Path(args.apply).name
        if isinstance(args.apply, str)
        else None
    )
    if args.apply and args.dry_run:
        parser.error("--apply and --dry-run cannot be combined")
    if args.target is None and not apply_manifest:
        parser.error("target is required unless --apply names a manifest")
    if args.root_override and args.target is None and not apply_manifest:
        parser.error("target is required with --root")
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    if args.jobs != 1:
        parser.error("only --jobs 1 is currently supported")
    manifest_root = None
    manifest_object_root = None
    if apply_manifest:
        try:
            manifest_data = json.loads(apply_manifest.read_text(encoding="utf-8"))
            manifest_root = Path(manifest_data["root"])
            if manifest_data.get("object_root"):
                manifest_object_root = Path(manifest_data["object_root"])
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
        ) as error:
            print(f"oxygen-migrate failed: {error}", file=sys.stderr)
            return 1
    root = (
        manifest_root
        if manifest_root and not args.root_override
        else args.root_override or DEFAULT_ROOT
    )
    started_monotonic = time.monotonic()
    if args.nice:
        try:
            os.nice(args.nice)
        except OSError as error:
            print(f"could not apply --nice {args.nice}: {error}", file=sys.stderr)
            return 2
    print(
        f"oxygen-migrate mode={'apply' if args.apply else 'dry-run'} "
        f"started={datetime.now(timezone.utc).isoformat()} "
        f"root={root.resolve()} "
        f"target={args.target or '.'} path={args.path or '.'} "
        f"limit={args.limit if args.limit is not None else 'all'} "
        f"object-root={(args.object_root or manifest_object_root or root / '.objects').resolve()}",
        flush=True,
    )
    if apply_manifest:
        selected = None
    elif args.target and args.path:
        selected = root / args.target / args.path
    elif args.path:
        selected = root / args.path
    elif args.target:
        selected = root / args.target
    else:
        selected = None
    try:
        count = migrate(
            root,
            dry_run=not bool(args.apply),
            selected=selected,
            limit=args.limit,
            object_root=args.object_root,
            manifest=args.manifest,
            apply_manifest=apply_manifest,
            exclude=tuple(args.exclude),
            verbose=args.verbose,
        )
    except KeyboardInterrupt:
        print("oxygen-migrate interrupted; no completion summary", file=sys.stderr)
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"oxygen-migrate failed: {error}", file=sys.stderr)
        return 1
    action = "would migrate" if not args.apply else "migrated"
    elapsed = time.monotonic() - started_monotonic
    rate = count / elapsed if elapsed else 0.0
    print(
        f"{action} {count} files elapsed={elapsed:.2f}s rate={rate:.2f}/s",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
