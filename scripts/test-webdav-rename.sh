#!/usr/bin/env bash
set -euo pipefail

if (($# >= 1)); then
  WEBDAV_ROOT=$1
else
  read -r -p 'WebDAV root URL (maps to /var/services/photo): ' WEBDAV_ROOT
fi
[[ -n "$WEBDAV_ROOT" ]] || { echo 'WebDAV root URL is required.' >&2; exit 2; }
SSH_HOST=${2:-oxygen}
REMOTE_DIR=${3:-/var/services/photo/2022}
YEAR=${REMOTE_DIR##*/}
TEST_DIR="$REMOTE_DIR/.webdav-test"
BASE_URL=${WEBDAV_ROOT%/}/$YEAR/.webdav-test
USER_NAME=$(read -r -p 'WebDAV username: ' value && printf '%s' "$value")
read -r -s -p 'WebDAV password: ' PASSWORD
printf '\n'

TEST_FILE=$(mktemp)
trap 'rm -f "$TEST_FILE"; ssh "$SSH_HOST" "rm -rf -- $(printf %q "$TEST_DIR")" >/dev/null 2>&1 || true' EXIT
CURL_AUTH=(--anyauth --user "$USER_NAME:$PASSWORD")

printf 'Checking WebDAV read access...\n'
auth_status=$(curl -sS -o /dev/null -w '%{http_code}' \
  "${CURL_AUTH[@]}" -X PROPFIND -H 'Depth: 0' "$WEBDAV_ROOT")
printf 'PROPFIND returned HTTP %s\n' "$auth_status"
if [[ $auth_status == 401 ]]; then
  echo 'WebDAV authentication failed; server offered:' >&2
  curl -sS -D - -o /dev/null "${CURL_AUTH[@]}" -X PROPFIND \
    -H 'Depth: 0' "$WEBDAV_ROOT" | grep -i '^www-authenticate:' >&2 || true
  echo 'No file test was attempted.' >&2
  exit 1
fi
if [[ $auth_status != 200 && $auth_status != 207 ]]; then
  echo 'WebDAV read access failed; no file test was attempted.' >&2
  exit 1
fi

ssh "$SSH_HOST" "mkdir -p -- $(printf %q "$TEST_DIR") && printf test > $(printf %q "$TEST_DIR/a.jpg") && chmod 0777 $(printf %q "$TEST_DIR") && chmod 0644 $(printf %q "$TEST_DIR/a.jpg")"

printf 'Testing WebDAV MOVE with a 0644 source file...\n'
status=$(curl -sS -o /dev/null -w '%{http_code}' \
  "${CURL_AUTH[@]}" \
  -X MOVE \
  -H "Destination: $BASE_URL/b.jpg" \
  "$BASE_URL/a.jpg")
printf 'MOVE returned HTTP %s\n' "$status"

if [[ $status != 201 && $status != 204 ]]; then
  echo 'Rename test failed.' >&2
  exit 1
fi

if ! ssh "$SSH_HOST" "test -f $(printf %q "$TEST_DIR/b.jpg")"; then
  echo 'MOVE reported success, but the destination file was not found.' >&2
  exit 1
fi

printf 'Testing WebDAV PUT...\n'
printf 'test write\n' >"$TEST_FILE"
put_status=$(curl -sS -o /dev/null -w '%{http_code}' \
  "${CURL_AUTH[@]}" \
  -T "$TEST_FILE" \
  "$BASE_URL/c.jpg")
printf 'PUT returned HTTP %s\n' "$put_status"

if [[ $put_status != 200 && $put_status != 201 && $put_status != 204 ]]; then
  echo 'Write test failed.' >&2
  exit 1
fi

echo 'WebDAV MOVE and PUT succeeded.'
