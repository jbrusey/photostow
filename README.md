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

```sh
uv sync
make check
```

Defaults assume:

```text
OXYGEN_HOST=oxygen
OXYGEN_DIR=/var/services/photo
OXYGEN_LEDGER=photos-oxygen-sha
PHOTOS_LIBRARY=$HOME/Pictures/Photos Library.photoslibrary
REVIEW_DIR=review
```

Override any of these on the `make` command line if needed.

## Commands

### `make update-oxygen-ledger`

Incrementally updates the local `photos-oxygen-sha` from Synology. It lists files under `/var/services/photo`, compares paths already in the ledger, and hashes only new paths.

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

### `make copy-reviewed-to-oxygen`

Copies the remaining files in `review/` into Synology year folders under `/var/services/photo`. This does not update the ledger by itself.

### `make archive-reviewed`

Preferred final step. Runs:

```text
copy-reviewed-to-oxygen -> update-oxygen-ledger -> install-oxygen-ledger
```

Use this instead of `make copy-reviewed-to-oxygen` unless you deliberately want to inspect/update the ledger separately.

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

See [PLAN.md](PLAN.md).
