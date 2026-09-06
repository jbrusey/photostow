from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photostow.oxygen import default_object_root, prune_local_ledger


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-ledger-prune")
    parser.add_argument("--root", type=Path, default=Path("/var/services/photo"))
    parser.add_argument("--object-root", type=Path)
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--keep", type=int, default=5)
    args = parser.parse_args()
    try:
        root = args.root.resolve()
        object_root = (args.object_root or default_object_root(root)).resolve()
        ledger = args.ledger or object_root / "ledger" / "photos-oxygen-sha"
        before, after = prune_local_ledger(root, ledger, args.keep, object_root)
    except (OSError, ValueError) as error:
        print(f"oxygen-ledger-prune failed: {error}", file=sys.stderr)
        return 1
    print(f"pruned ledger rows {before} -> {after}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
