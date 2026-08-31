# Technical review

Implementation is not ready for production. `PLAN.md` is the source of truth; this file records the remaining checks and the current gaps.

## Current implementation gaps

- Migration apply now discovers, hashes, and publishes one file at a time. Dry-run and manifest generation still intentionally collect the selected records.
- Manifest generation and manifest-authoritative apply remain available for reviewed legacy workflows; streaming apply is now the normal path.
- Migration enforces source/object inode identity and expected link counts, records failures in append-safe JSONL, and supports halt or reviewed continuation.
- Migration revalidates source content before publication; portable final replacement still has a documented TOCTOU limitation.
- Ingest reuses only verified objects with no existing visible reference; it still needs the planned size-only fast path and explicit failure-list/safe-verification options.
- Ingest rejects symlinked source and destination parents before resolving paths.
- There is no visible-reference report or sorted digest export.
- Oxygen entry points use Python 3.10 type-syntax features, although Oxygen runs Python 3.9.

## Local checks

Run:

```sh
make check
```

Local tests do not replace Oxygen acceptance. The current test suite covers streaming commits, link counts, fail lists, duplicate-reference handling, and publish races; reference reporting, digest export, and real Oxygen behavior remain outstanding.

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
