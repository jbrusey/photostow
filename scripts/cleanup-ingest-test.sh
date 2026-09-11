#!/bin/sh
set -eu

[ "${CONFIRM:-}" = YES ] || {
    echo "refusing cleanup; run CONFIRM=YES make cleanup-ingest-test" >&2
    exit 1
}
host=${OXYGEN_HOST:-oxygen}
root=/var/services/photo/.photostow-ingest-test
ssh "$host" "test -d '$root' && rm -rf -- '$root'"
echo "removed $host:$root"
