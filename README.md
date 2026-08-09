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
                                                          make copy-reviewed-to-oxygen
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

Incrementally updates `photos-oxygen-sha` from Synology. It lists files under `/var/services/photo`, compares paths already in the ledger, and hashes only new paths.

Run this before comparing a laptop Photos library.

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

Copies the remaining files in `review/` into Synology year folders under `/var/services/photo`.

### `make duplicate-groups`

Writes duplicate content groups on oxygen to:

```text
duplicate-groups.txt
```

This uses the ledger hashes and only reports files that currently exist on Synology.

## Lower-level commands

The Makefile wraps the CLI. For custom paths, use `uv run photostow --help` and subcommand help.

See [PLAN.md](PLAN.md).
