# photostow

Archive Photos.app originals to a Synology photo share without trusting filenames or dates. The archive snapshot is `photos-oxygen-sha`; it lives with its backups under `/volume1/photostow/ledger`, outside the Photos tree.

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
oxygen-ledger-prune --help
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

Copies the local `photos-oxygen-sha` back to oxygen as `/volume1/photostow/ledger/photos-oxygen-sha`. Before replacing it, oxygen rotates compressed backups:

```text
photos-oxygen-sha.1.gz
photos-oxygen-sha.2.gz
...
```

Default retention is 5 backups; override with `LEDGER_BACKUPS=10`.

### `oxygen-ledger-prune`

Run this on Oxygen after deleting visible files. It scans `/var/services/photo`
locally, removes stale rows from `/volume1/photostow/ledger/photos-oxygen-sha`,
and rotates compressed backups without transferring the archive listing over SSH:

```sh
oxygen-ledger-prune
```

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

### `make profile-ingest-test`

Use this for a small, disposable upload and ledger-ingest benchmark. Put the
sample files in `ingest-test-images/`, preferably grouped into year folders:

```sh
mkdir -p ingest-test-images/2024 ingest-test-images/2025
# Copy or hardlink a representative sample into those directories.
make profile-ingest-test
```

The target refuses to run if the dedicated remote test root already exists.
It profiles source hashing, the copy, and the remote ledger update separately,
writing timings and a manifest to `ingest-test-profile/`. It never installs the test ledger as
the production ledger. After checking the results, remove only the disposable
remote tree with:

```sh
CONFIRM=YES make cleanup-ingest-test
```

Override the host with `OXYGEN_HOST=...`; override the local sample directory
with `INGEST_TEST_IMAGES=...`.

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

## Removing the old object-store links

The former object-store design is no longer supported. On Oxygen, stop archive
writers and identify the actual old object root (formerly
`/volume1/photostow`). First inspect only object files that have another hardlink:

```sh
OBJECT_ROOT=/volume1/photostow
find "$OBJECT_ROOT/sha256" -type f -links +1 -print
```

After reviewing the list, remove those object-store directory entries and empty
shards:

```sh
find "$OBJECT_ROOT/sha256" -type f -links +1 -delete
find "$OBJECT_ROOT/sha256" -depth -type d -empty -delete
```

This removes the object-store names, not the visible archive files or their
contents. Do not delete link-count-one objects: they may be the only remaining
copy. Preserve them until they have been compared with the archive and
explicitly handled. Back up and separately review old migration state, failure
logs, and the object-store ledger before removing them.

The supported Oxygen command is now only:

```sh
oxygen-ledger-prune --root /var/services/photo \
  --ledger /volume1/photostow/ledger/photos-oxygen-sha
```

See [PLAN.md](PLAN.md) for the design rationale and cleanup procedure.
