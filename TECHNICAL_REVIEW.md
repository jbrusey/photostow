# Technical review

Implementation is not ready for production. `PLAN.md` is the source of truth; this file records the remaining checks and the current gaps.

## Current implementation gaps

- Migration still discovers and hashes the complete selection before publishing files. It must process one file at a time.
- Migration still supports manifest-authoritative apply. The normal workflow must use streaming apply; manifest generation and apply should be removed.
- Migration does not enforce the required source/object inode and link-count rules.
- Migration can continue after publish errors, has no durable fail list, and can leave an invalid object after a source changes during publication.
- Ingest fully hashes existing objects and creates another visible hardlink when the object already has a visible reference. It needs the size fast path, duplicate fail-list behavior, and explicit safe verification.
- Ingest must reject symlinked source and destination parents before resolving paths.
- There is no visible-reference report or sorted digest export.
- Oxygen entry points use Python 3.10 type-syntax features, although Oxygen runs Python 3.9.

## Local checks

Run:

```sh
make check
```

Local tests do not replace Oxygen acceptance. The current test suite covers the existing implementation and must gain tests for streaming commits, link counts, fail lists, duplicate-reference handling, safe verification, reference reporting, and digest export.

## Required Oxygen acceptance

Before production migration:

- [ ] Confirm the object root is on the same filesystem and has sufficient free bytes, inodes, and hardlink support.
- [ ] Test the representative migration on an isolated directory and one real year.
- [ ] Interrupt and rerun migration; confirm completed files remain valid and no manifest is required.
- [ ] Run `oxygen-verify` and compare object counts, visible counts, hashes, and link counts before and after.
- [ ] Confirm corrupt, malformed, orphaned, and unexpectedly referenced objects are reported and retained.
- [ ] Test Synology Photos indexing, `@eaDir`, Pixette visibility, and Object Store exclusion.
- [ ] Test WebDAV open, rename, and write behavior with Pixette's account.
- [ ] Record permissions, ownership, ACLs, timestamps, and xattrs before and after migration.
- [ ] Keep `photos-oxygen-sha` until object-store acceptance and rollback checks pass.
- [ ] Test reviewed laptop ingest and digest export before retiring ledger-based workflows.
- [ ] Confirm RAID-0 is not treated as backup and record the separate backup plan.

Production migration requires successful local checks, all Oxygen checks above, and explicit review of the results. The archive root spelling is `/var/services/photo`.
