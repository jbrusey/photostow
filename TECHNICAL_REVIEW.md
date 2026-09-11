# Technical review

The supported design is a conventional visible archive plus the
`photos-oxygen-sha` ledger. The former content-addressed object-store
prototype is not part of the production workflow.

## Current implementation gaps

- Production Oxygen/Synology acceptance remains outstanding because the
  environment is unavailable.
- The ledger is line-based; a database index is unnecessary unless measured
  lookup time becomes a problem.
- Remote filenames are expected to be UTF-8 and ledger paths cannot contain
  unsupported newlines.

## Local checks

Run:

```sh
make check
```

Local tests use mocks and do not replace live Oxygen acceptance.

## Required Oxygen acceptance

Before relying on the archive operationally:

- [ ] Confirm `/var/services/photo` and the ledger location are writable by the
      intended account.
- [ ] Run the reviewed copy workflow on an isolated directory and one real year.
- [ ] Confirm interrupted copies can be rerun without replacing reviewed files.
- [ ] Confirm Synology Photos indexing and Pixette/WebDAV behavior.
- [ ] Confirm duplicate deletion verifies size and `cmp` before removal.
- [ ] Confirm ledger pruning removes rows only for genuinely absent paths.
- [ ] Remove old object-store links using the procedure in `PLAN.md`; preserve
      link-count-one objects until they are explicitly reviewed.
- [ ] Confirm RAID-0 is not treated as backup and record the separate backup
      plan.

Production use requires successful local checks, the Oxygen checks above, and
explicit review of the results. The archive root spelling is
`/var/services/photo`.
