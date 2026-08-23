from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from photostow.oxygen import gc_summary


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-gc")
    parser.add_argument("object_root", type=Path)
    parser.add_argument(
        "--root",
        type=Path,
        help="archive root for locking (inferred from .objects)",
    )
    args = parser.parse_args()
    started = time.monotonic()
    if args.root is None and args.object_root.name != ".objects":
        parser.error("--root is required for a custom object root")
    root = args.root or args.object_root.resolve().parent
    print(
        f"oxygen-gc mode=report root={root.resolve()} object-root={args.object_root.resolve()}",
        flush=True,
    )
    try:
        candidates, retained = gc_summary(args.object_root, root=root)
    except KeyboardInterrupt:
        print("oxygen-gc interrupted; report incomplete", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"oxygen-gc failed: {error}", file=sys.stderr)
        return 1
    for path in candidates:
        print(path)
    elapsed = time.monotonic() - started
    print(f"candidates={len(candidates)} retained={retained} elapsed={elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
