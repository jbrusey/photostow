#!/bin/sh
set -eu

source=${INGEST_TEST_IMAGES:-ingest-test-images}
host=${OXYGEN_HOST:-oxygen}
root=${INGEST_TEST_ROOT:-/var/services/photo/.photostow-ingest-test}
out=${INGEST_TEST_PROFILE:-ingest-test-profile}

[ "$root" = /var/services/photo/.photostow-ingest-test ] || {
    echo "INGEST_TEST_ROOT must remain /var/services/photo/.photostow-ingest-test" >&2
    exit 1
}
[ -d "$source" ] || { echo "image directory not found: $source" >&2; exit 1; }
find "$source" -type f -print -quit | grep -q . || {
    echo "no images found under: $source" >&2
    exit 1
}

mkdir -p "$out"
ssh "$host" "test ! -e '$root'" || {
    echo "remote test root already exists; run 'make cleanup-ingest-test' first" >&2
    exit 1
}

count=$(find "$source" -type f | wc -l | tr -d ' ')
bytes=$(find "$source" -type f -exec stat -f '%z' {} + | awk '{s += $1} END {print s + 0}')
printf 'files=%s\nbytes=%s\nsource=%s\nhost=%s\nroot=%s\n' \
    "$count" "$bytes" "$source" "$host" "$root" > "$out/manifest.txt"

/usr/bin/time -l uv run photostow hash "$source" \
    > "$out/source-hash.stdout" 2> "$out/source-hash.time"
/usr/bin/time -l uv run photostow copy-tree "$source" "$host" "$root" \
    > "$out/copy.stdout" 2> "$out/copy.time"
/usr/bin/time -l uv run photostow update-remote-ledger "$host" "$root" \
    "$out/ledger" > "$out/ledger.stdout" 2> "$out/ledger.time"

echo "profile written to $out/ (remove remote files with 'make cleanup-ingest-test')"
