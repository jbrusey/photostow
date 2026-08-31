from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from photostow.oxygen import default_object_root, ingest


def main() -> int:
    parser = argparse.ArgumentParser(prog="oxygen-ingest")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--object-root", type=Path)
    parser.add_argument(
        "--safe-verify",
        action="store_true",
        help="rehash an existing object before reusing it",
    )
    args = parser.parse_args()
    destination = (
        args.destination
        if args.destination.is_absolute()
        else args.root / args.destination
    ).resolve()
    started = time.monotonic()
    print(
        f"oxygen-ingest mode=apply root={args.root.resolve()} "
        f"source={args.source.resolve()} destination={destination} "
        f"object-root={(args.object_root or default_object_root(args.root)).resolve()}",
        flush=True,
    )
    try:
        if args.safe_verify:
            digest = ingest(
                args.source,
                destination,
                args.root,
                args.object_root,
                safe_verify=True,
            )
        else:
            digest = ingest(args.source, destination, args.root, args.object_root)
    except KeyboardInterrupt:
        print("oxygen-ingest interrupted; publish incomplete", file=sys.stderr)
        return 130
    except (OSError, ValueError) as error:
        print(f"oxygen-ingest failed: {error}", file=sys.stderr)
        return 1
    elapsed = time.monotonic() - started
    print(f"ingested {destination} sha256={digest} elapsed={elapsed:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
