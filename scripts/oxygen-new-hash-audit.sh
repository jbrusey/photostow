#!/usr/bin/env bash
set -euo pipefail

HOST=${1:-oxygen}
ROOT=${2:-/volume1/photo}
LEDGER=${3:-photos-oxygen-sha}
WORKDIR=${4:-audit-new-remote-$(date +%Y%m%d-%H%M%S)}

uv run photostow audit-new-remote "$HOST" "$ROOT" "$LEDGER" --workdir "$WORKDIR"

echo
echo "Review $WORKDIR/unknown-paths.txt first."
echo "If it looks right, hash only those unknown paths with:"
echo "  uv run photostow audit-new-remote '$HOST' '$ROOT' '$LEDGER' --workdir '$WORKDIR' --hash"
