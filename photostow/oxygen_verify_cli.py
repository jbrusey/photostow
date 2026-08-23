from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from photostow.oxygen import verify_objects


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-verify")
    parser.add_argument("object_root", type=Path)
    parser.add_argument("--path", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    if args.limit is not None and args.limit < 0:
        parser.error("--limit must be non-negative")
    print(
        f"oxygen-verify root={args.object_root.resolve()} "
        f"path={args.path or '.'} "
        f"limit={args.limit if args.limit is not None else 'all'}",
        flush=True,
    )
    try:
        errors = verify_objects(
            args.object_root, args.path, args.limit, progress=args.verbose
        )
    except KeyboardInterrupt:
        print("oxygen-verify interrupted; verification incomplete", file=sys.stderr)
        return 130
    except ValueError as error:
        print(f"oxygen-verify failed: {error}", file=sys.stderr)
        return 1
    for message in errors:
        print(message, file=sys.stderr)
    elapsed = time.monotonic() - started
    print(f"verified {len(errors) == 0} errors={len(errors)} elapsed={elapsed:.2f}s")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
