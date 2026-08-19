from __future__ import annotations

import argparse
import sys
from pathlib import Path

from photostow.oxygen import migrate


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-migrate")
    parser.add_argument("root")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    count = migrate(Path(args.root), dry_run=not args.apply)
    action = "would migrate" if not args.apply else "migrated"
    print(f"{action} {count} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
