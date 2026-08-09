# AGENTS.md

Notes for future coding-agent sessions in this repo.

## Synology path convention

Use the canonical Synology photo path everywhere:

```text
/var/services/photo
```

Do not mix in `/volume1/photo` in code, tests, docs, or generated ledgers. On oxygen:

```text
/var/services/photo -> /volume1/photo
```

They are the same files, but string path comparisons in `photos-oxygen-sha` depend on consistent spelling.

## Oxygen is large and slow

The photo archive is large. Avoid broad commands unless needed:

- Avoid full `du -sh /var/services/photo`; it can take minutes or time out.
- Avoid full archive rehashes.
- Prefer incremental commands:
  - `make update-oxygen-ledger`
  - `make prune-oxygen-ledger`
  - `make duplicate-groups`
- If a full scan is required, explain what will run and estimate size/time first.

## Ledger safety

`photos-oxygen-sha` is the archive truth. After copying reviewed files, use:

```sh
make archive-reviewed
```

rather than `make copy-reviewed-to-oxygen`, so the ledger is updated and installed back on oxygen.

Remote ledger installs rotate compressed backups:

```text
photos-oxygen-sha.1.gz
photos-oxygen-sha.2.gz
...
```

## Review before copy

Do not copy missing Photos files straight to oxygen by default. Preferred workflow:

```sh
make update-oxygen-ledger
make missing
make stage-review
# user inspects/deletes files in review/
make archive-reviewed
```

`review/` is hardlinked to Photos originals. Delete unwanted review files only; do not edit them.

## Duplicate deletion

Duplicate reports order the preferred keep file first. Deletion must verify size and `cmp` before removing anything. Any verification failure should stop processing.

Dry run:

```sh
make delete-duplicates
```

Actual delete requires explicit CLI `--yes`.

## Generated files

Large/generated files are ignored: ledgers, review trees, duplicate reports, oxygen audit files. Do not add them to git.
