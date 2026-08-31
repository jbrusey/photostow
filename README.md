# photostow

Archive Photos.app originals to a Synology photo share without trusting filenames or dates. The archive truth is SHA-256 content hashes stored in `photos-oxygen-sha`.

## Typical flow

```text
Synology ledger ──make update-oxygen-ledger──┐
                                             v
Photos library ──make missing──> missing.tsv ──make stage-review──> review/YYYY/files
                                                                      │
                                                                      │ delete unwanted files
                                                                      v
                                                          make archive-reviewed
```

## Setup

Development uses `uv` and installs the optional `dev` tools:

```sh
uv sync --extra dev
make check
# Full technical gate, including lock and diff checks:
make review
```

Oxygen only needs the package and its standard-library runtime. After pulling
the repository on Oxygen, install it without the development extra:

```sh
git pull --ff-only
python3 --version  # 3.9.14
python3 -m pip install --user -e .
oxygen-migrate --help
oxygen-ingest --help
oxygen-verify --help
oxygen-gc --help
```

Do not run `uv` or install the `dev` extra on Oxygen. If a user-installed
command is not found, add Python's user script directory to `PATH`.

Defaults assume:

```text
OXYGEN_HOST=oxygen
OXYGEN_DIR=/var/services/photo
OXYGEN_LEDGER=photos-oxygen-sha
PHOTOS_LIBRARY=$HOME/Pictures/Photos Library.photoslibrary
REVIEW_DIR=review
```

Override any of these on the `make` command line if needed. Remote path
output is currently expected to be UTF-8; non-UTF-8 filenames require production
acceptance testing before support is claimed.

## Commands

### `make update-oxygen-ledger`

Incrementally updates the local `photos-oxygen-sha` from Synology. It lists files under `/var/services/photo`, compares paths already in the ledger, and hashes only new paths. Remote paths use NUL-delimited transport and are expected to be UTF-8; malformed discovery or SSH output fails before path/stat parsing with a concise `ValueError` diagnostic. Containment escapes are rejected separately. Remote hashing requires `sha256sum --zero` and rejects newline-containing paths because the ledger is line-based. Laptop CLI validation and filesystem failures return exit 1 with a concise stderr diagnostic. For `remote discovery output is not UTF-8` or `remote SSH output is not UTF-8`, check the remote locale/transport bytes; for `remote discovery path escapes root` or `remote audit path escapes root`, check the requested root and reported paths before retrying. Rerun focused local validation with `uv run pytest tests/test_remote.py tests/test_audit.py tests/test_cli.py`; these tests use mocks, and `ASSUMPTIONS.md` plus local results are not production evidence; they do not replace live Oxygen acceptance, and local tests alone do not approve a production migration; the CLI test `test_documented_status_files_exist` also verifies the linked status files exist, and its README/technical-review name parity is checked automatically; if that check fails, update `README.md` and `TECHNICAL_REVIEW.md` together with the documented test name; the parity assertion lives in [`tests/test_cli.py`](tests/test_cli.py). `ValueError`, `UnicodeDecodeError`, and `OSError` validation/filesystem failures exit 1; the direct filesystem guard is `test_main_reports_os_error` in [`tests/test_cli.py`](tests/test_cli.py), and stderr preserves errno details such as `[Errno 13]`. For example, a validation failure emits: `photostow failed: remote discovery output is not UTF-8`; correct the remote transport before rerunning; do not retry unchanged input. Decode failures retain codec, byte position, and reason details. This is the same action boundary recorded in the technical review and covered by the parity assertions in [`tests/test_cli.py`](tests/test_cli.py), especially `test_readme_names_documented_status_test`; keep this corrective wording synchronized.

See [TECHNICAL_REVIEW.md](TECHNICAL_REVIEW.md) for current acceptance status and [ASSUMPTIONS.md](ASSUMPTIONS.md) for unavailable Oxygen prerequisites. See [PROGRESS.md](PROGRESS.md) for parity-test iteration evidence; repair a stale history link by restoring `PROGRESS.md` and rerunning the focused CLI test.

Run this before comparing a laptop Photos library.

### `make install-oxygen-ledger`

Copies the local `photos-oxygen-sha` back to oxygen as `/var/services/photo/photos-oxygen-sha`. Before replacing it, oxygen rotates compressed backups:

```text
photos-oxygen-sha.1.gz
photos-oxygen-sha.2.gz
...
```

Default retention is 5 backups; override with `LEDGER_BACKUPS=10`.

### `make prune-oxygen-ledger`

Removes ledger rows whose files no longer exist on Synology. Use after deleting whole staging folders such as `incoming/carbon`.

### `make missing`

Reads the local Photos database read-only, hashes present originals, and writes photos absent from the Synology ledger to:

```text
missing.tsv
```

### `make stage-review`

Builds a local review tree from `missing.tsv`:

```text
review/2023/file.heic
review/2024/file.mov
```

The files are hardlinks to Photos originals, so this is quick and does not duplicate disk space. Delete unwanted files from `review/`; do not edit them.

### `make archive-reviewed`

Copies the remaining files in `review/` into Synology year folders under `/var/services/photo`,
then updates and reinstalls the ledger. This is the only supported reviewed-copy workflow.

### `make duplicate-groups`

Writes duplicate content groups on oxygen to:

```text
duplicate-groups.txt
```

This uses the ledger hashes and only reports files that currently exist on Synology. Within each group, the first path is the preferred one to keep:

1. filenames containing `pixette_removed`
2. standard year-folder files like `/var/services/photo/2023/name.jpg`
3. everything else

### `make delete-duplicates`

Dry-run duplicate deletion from `duplicate-groups.txt`. Before each delete candidate it checks on oxygen that:

- both files still exist
- file sizes match
- `cmp` says the contents match

Any failed check stops processing. To actually delete, run the lower-level command explicitly:

```sh
uv run photostow delete-duplicates oxygen duplicate-groups.txt --yes
```

## Lower-level commands

The Makefile wraps the CLI. For custom paths, use `uv run photostow --help` and subcommand help.

## Content-addressed Oxygen commands

These commands run locally on Oxygen with Python 3.9.14. Migration applies
changes by default; use `--dry-run` for a preview. Ingest applies changes,
while verify and GC are report-only:

```sh
oxygen-migrate 2006 --dry-run --limit 10 --exclude 'incoming-*' \
  --jobs 1 --nice 10 --manifest trial.json
oxygen-migrate --apply trial.json
oxygen-ingest incoming.jpg /var/services/photo/2025/incoming.jpg \
  --root /var/services/photo
oxygen-verify /volume1/photostow --limit 10 --verbose
oxygen-gc /volume1/photostow
```

A positional migration target is relative to `/var/services/photo`; `--path`
adds a file or subtree beneath that target. For another archive, use the
explicit form `oxygen-migrate --root /archive 2006 --path photo.jpg`.

The default object store is `/volume1/photostow` for the canonical archive
`/var/services/photo`. Use `--object-root` to choose another location on the same
filesystem; local non-canonical roots keep their `.objects` store by default.
For a custom object-root name, pass the archive root to GC as well:
`oxygen-gc /archive-objects --root /var/services/photo`.
Migration hashes serially (`--jobs 1`) and lowers CPU priority by default;
use `--verbose` for one line per discovered/hashed file. Without it, output is
bounded to startup and summary progress. Apply failures are appended as JSONL
to `migration-failures.jsonl` by default; use `--failure-list` to choose the
path and `--continue-on-error` to process later files. Apply processes files
one at a time; `--limit` bounds the streamed selection;
it does not make a large directory walk cheap. Use `--path` for a genuinely small
trial subtree. A higher `--jobs` value is rejected
until parallel hashing is implemented. Repeat
`--exclude PATTERN` to omit files or subtrees.
Review a manifest before applying it; apply refuses files whose identity, size,
mtime, or digest changed. Migration refuses multiple visible references to one
object and records those paths for review. `oxygen-gc` is report-only and does not delete files. It verifies the object
store first, lists link-count-one candidates, and reports retained objects with
other link counts; malformed or corrupt stores produce no candidate report.

Visible photos and content objects are hardlinks, so they share inode metadata.
Replacing a visible file can change its permissions, timestamps, ownership, ACLs,
and extended attributes. Object contents are protected by policy and verification,
not by separate permissions: editing any hardlink edits the shared inode. Test
Pixette/WebDAV rename and write behavior on a representative directory before
migrating production data. Passing local `make review` is not production approval;
Production Oxygen/Synology acceptance remains pending because the environment is unavailable; complete the technical-review checklist before approval.

See [PLAN.md](PLAN.md), the [technical review checklist](TECHNICAL_REVIEW.md),
and [ASSUMPTIONS.md](ASSUMPTIONS.md) for environment limits.
