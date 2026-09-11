# photostow plan

## Goal

Maintain the Synology photo archive as a conventional year/filename tree and
keep `photos-oxygen-sha` as the archive's content ledger. The canonical archive
root is `/var/services/photo`.

The ledger maps each visible archive path to its SHA-256 digest. It is the
authority used by the laptop workflow for missing-file detection, incremental
remote hashing, and duplicate reports. It is intentionally retained: a file's
hardlink or inode does not reveal its digest.

## Supported workflow

```text
Synology ledger ──update──┐
                          v
Photos library ──missing──> review ──archive-reviewed──> year folders + ledger
```

The supported commands are the laptop `photostow` commands and
`oxygen-ledger-prune` for pruning the ledger locally on Oxygen. New archive
files are copied into the visible tree; they are not placed in a separate
object store.

### Ledger maintenance

- Update hashes only for newly discovered remote paths.
- Prune rows for paths deleted from the archive after visible deletions.
- Install the reviewed ledger back to Oxygen with rotating compressed backups.
- Keep the ledger outside the photo tree when possible so Synology Photos does
  not index it.

Ledger updates are incremental but remote discovery still walks the archive.
That is preferable to a second object namespace whose visible paths still need
an index to map back to their hashes.

### Review and copy

`make missing` hashes present Photos originals and compares their digests with
the ledger. `make stage-review` creates a temporary local review tree using
hardlinks to Photos originals; deleting review entries does not delete the
originals. `make archive-reviewed` copies the remaining review files to
`/var/services/photo`, then refreshes and installs the ledger.

The review hardlinks are only a temporary local staging optimization. They are
not an archive representation and must not be confused with the removed object
store design.

### Duplicates

Duplicate reports use ledger digests and verify size and `cmp` before any
requested deletion. The preferred path is listed first. Deleting a duplicate
visible file does not require object-store or inode bookkeeping; prune the
ledger afterwards.

## Removing the old object-store links

Do this on Oxygen, while no photo-management or migration process is writing
the archive. First identify the exact object root used by the old deployment;
the former default was `/volume1/photostow` (use the actual configured path).

Do not delete the object files blindly: an object with link count one may be
the only remaining copy of its content. The following dry run lists only object
files with another hardlink, without reading file contents:

```sh
OBJECT_ROOT=/volume1/photostow
find "$OBJECT_ROOT/sha256" -type f -links +1 -print
```

After checking the list, remove only those object-store directory entries:

```sh
find "$OBJECT_ROOT/sha256" -type f -links +1 -delete
find "$OBJECT_ROOT/sha256" -depth -type d -empty -delete
```

This unlinks the object-store names; it does not remove the visible archive
hardlinks or their contents. Stop instead if any listed object has no visible
copy, or if the old deployment did not maintain the expected link counts.
Keep any link-count-one object files until their contents have been compared
with the archive and explicitly dealt with. Remove obsolete migration state,
failure logs, and the now-unused object-store ledger only after taking a backup
and confirming they are not needed for audit or rollback.

## Deliberately not planned

There is no content-addressed object store, Oxygen migration, object GC,
object verification command, or Oxygen ingest command. Adding one later would
require a demonstrated deduplication requirement and a separate decision about
per-path metadata, Synology/WebDAV write behavior, indexing, and a durable
path-to-digest index.
