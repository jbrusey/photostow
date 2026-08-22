# photostow plan

## Goal

Maintain the Synology photo archive as a content-addressed store while keeping the
existing year/filename tree as the user-facing and Pixette-indexed view.

The archive root is:

```text
/var/services/photo
```

The object store uses this layout:

```text
<object-root>/sha256/ab/cdef...
```

`<object-root>` may be `/var/services/photo/.objects`, but it need not be inside
the photo share. A location elsewhere on the same filesystem may better avoid
Synology Photos/media-indexing and `@eaDir` activity. Resolve
`/var/services/photo` on Oxygen and test both placement and indexing behavior
before choosing the production location.

Each visible photo is a hardlink to its content object. SHA-256 is the identity;
filenames, dates, and directory names are not identity. Object contents must not
be modified after ingestion. This is an invariant and operating policy, not a
separate-permission guarantee, because hardlinks share an inode and permissions.

## Commands

Commands beginning with `oxygen-` run on Oxygen and use local filesystem
operations. They must not require SSH, `uv`, or third-party Python packages.
The current Synology has Python 3.8, so the Oxygen entry points must remain
compatible with that runtime.

Planned commands:

```text
oxygen-migrate   existing visible tree -> object store
oxygen-ingest    incoming files -> object store and visible tree
oxygen-verify    check object names and contents
oxygen-gc        optionally remove unreferenced objects
```

The existing laptop-side Photos workflow can remain separate while this is
introduced and tested.

## Migration

`oxygen-migrate` processes an existing directory such as `2006`:

1. Exclude the object store, `@eaDir`, ledgers, `.DS_Store`, symlinks, and
   unreadable paths.
2. Select files deterministically in sorted path order.
3. Hash selected files and map each digest to
   `<object-root>/sha256/ab/cdef...`.
4. Create the object hardlink if absent.
5. Replace duplicate visible physical copies with hardlinks to that object.
6. Leave names, visible paths, and year directories unchanged.
7. Remain idempotent.

The object store must be on the same filesystem as the visible tree. The
migration should check this before making changes.

### Safe, observable operation

Dry-run is the default and must be useful on a large archive. It must:

- Print a startup line before scanning.
- Print discovery and hashing progress periodically, including files processed,
  elapsed time, and the current path.
- Flush progress output so an SSH session shows activity.
- Report a final summary even when no files are selected.
- Never hash the whole archive before producing the first progress output.

Migration must support small, repeatable experiments:

```sh
oxygen-migrate 2006 --dry-run --limit 10 --manifest trial.json
oxygen-migrate 2006 --dry-run --path IMG_1234.JPG --manifest trial.json
oxygen-migrate 2006 --dry-run --path 08/15 --manifest trial.json
oxygen-migrate --root /some/test/archive 2006 --dry-run --manifest trial.json
```

`/var/services/photo` is the default root. `--root` is an explicit override for
testing or another deliberately selected archive; positional targets must remain
beneath the resolved root.

Required selection options:

- `--limit N`: process at most N files after deterministic selection.
- `--path PATH`: restrict processing to a path or subtree under the root.
- `--exclude PATTERN`: repeatable exclusion for an additional subtree/pattern.

The dry-run manifest records the reviewed selection, relative paths, digests,
device/inode identities, sizes, and relevant timestamps. Apply consumes that
manifest as its authoritative input rather than repeating selection options or
performing a new selection:

```sh
oxygen-migrate --apply trial.json
```

Apply refuses entries that have changed or no longer resolve beneath the
recorded root.

### Resource limits

The default migration must be conservative because Oxygen is also serving
Photos:

- Hash serially by default (`--jobs 1`); do not start one process per CPU.
- Support `--jobs N` only when explicitly requested.
- Support `--nice N`, defaulting to a lower CPU priority where available.
- Avoid loading the entire file list or hashes into memory.
- Make progress reporting cheap and bounded.
- Document expected disk reads and estimated duration for a selected subset.

If the platform lacks a requested scheduling feature, report that clearly rather
than silently pretending it was applied.

### Failure and race handling

Migration should fail safely when:

- A file disappears or changes while being processed.
- A target object exists but does not verify against its hash.
- A directory cannot be read.
- A hardlink cannot be created.
- The object store is on another filesystem.

For a source file, open without following symlinks, check `fstat` before and
after hashing through the open descriptor, and confirm that the pathname still
identifies the same device and inode. Apply also compares the file with the
reviewed manifest. These checks avoid a mandatory second full hash pass while
detecting ordinary changes during processing.

There remains an unavoidable race if another process writes after these checks.
The selected archive subtree therefore must not be written to during apply. A
failed file should be reported with its path and the run should exit nonzero
after processing the selected batch, without deleting unverified data.

Existing objects must be verified before reuse, at least in migration's safe
mode. A later `oxygen-verify` provides periodic full integrity checking.

### Concurrency and recovery

Only one Oxygen migration/ingest/GC operation may run at a time. Use a local
lock and release it on interruption. Each file operation should be atomic from
the visible tree's perspective: create the object first, create a temporary
hardlink in the destination directory, then replace the visible path. An
interrupted run may leave an extra object, but must not leave a visible path
pointing at a partial object.

The reviewed manifest is the apply transaction described above. An interrupted
or partially completed apply remains safe to rerun against the same manifest,
subject to its identity and metadata checks.

### Metadata and hardlink limitations

Hardlinks share inode metadata. Replacing duplicate visible files can therefore
change their permissions, timestamps, ownership, ACLs, and extended attributes
to those of the object inode. This must be documented and tested on Oxygen.
If per-path metadata is required by Pixette or WebDAV, hardlinks are not a
sufficient representation and the design must stop rather than silently lose
it.

Content immutability is a safety rule, not a separate-permission guarantee: any
account with write permission to a visible hardlink can modify the shared inode
and invalidate the object's digest. The intended no-in-place-edit policy and
permissions/ACL behavior through WebDAV must be tested before broad migration.

## Ingest

`oxygen-ingest` accepts incoming files and a target visible directory/year:

1. Hash each incoming file using the same before/after descriptor checks as
   migration.
2. If the object exists, verify and reuse it.
3. If it is absent and the source is on the object filesystem, create it with a
   race-safe hardlink.
4. If the source is on another filesystem, copy it to a unique temporary file in
   the object's destination directory, flush and close it, verify its size and
   SHA-256, and atomically rename it into place. Never publish or link a partial
   copy. If another operation created the object first, verify and reuse it.
5. Once a verified object exists, create the requested visible hardlink,
   preserving the requested filename.
6. Refuse in-place edits to existing objects.

Visible filename collision behavior is explicit:

```text
target absent                         create it
target exists with the same digest    no-op
target exists with a different digest refuse by default
```

Ingest must never silently overwrite or rename an existing visible file.

Ingest must use the same selection, progress, resource-limit, and race-safety
rules as migration.

## Verification and deletion

`oxygen-verify` should:

- Recompute every object hash.
- Confirm the pathname digest matches the content.
- Report corrupt, missing, malformed, and duplicate object paths.
- Exclude `@eaDir` and visible files from the object scan.
- Support `--path`, `--limit`, and progress output for small tests.

Deleting a visible hardlink does not delete its object. `oxygen-gc` may later
remove objects with no visible hardlinks, but only after a report/dry-run and
revalidation. Objects with unexpected link counts must be retained and
reported. GC is optional and should not be implemented until migration, ingest,
verification, and real operation have been proven over time.

## Logging and operational confidence

Default output and durable logging must be useful but bounded:

- Print the command mode, root, object root, selection, and start time.
- Print discovery/hashing progress at a bounded interval, such as every 30–60
  seconds, plus warnings, errors, and a final summary.
- Do not log every file, reuse, relink, or skip by default.
- `--verbose` may emit one line per file; `--debug` may add internal diagnostics.
- Durable logs are optional. Detached runs may redirect bounded default output
  so an SSH disconnect does not lose progress information.
- Limit retained logs by both count and total size; for example, retain at most
  the newest 10 completed logs and 10 MiB total. Failed-run retention may be
  longer but must remain bounded and configurable.
- Apply the same bounded-retention principle to successful manifests. Never
  rotate or delete an active run.
- End with a machine-readable exit status and a human summary. Zero means every
  selected file was handled and verified; nonzero means review the error list.
- Make Ctrl-C report whether the run stopped cleanly and what remains.

A small real-data trial is successful only when the user can compare before and
after counts, run `oxygen-verify`, confirm expected hardlink counts, and test the
result through the same Synology indexing and WebDAV/Pixette paths used in
practice.

## Other risks and invariants

- Check free bytes, free inodes, and hardlink support before apply.
- Keep the object namespace versioned by algorithm (`sha256`) and reject
  malformed object names.
- Treat corrupt, partial, orphaned, and unexpected object entries as reportable
  states; never silently reuse them.
- Protect against changes while hashing with the descriptor/stat checks above
  and enforce the operational no-writes-during-apply rule.
- Define how case-sensitive names, Unicode names, long paths, and unusual
  filenames behave before ingest.
- Do not assume a sorted `--limit` is cheap: selection may require a full
  directory walk before hashing. Report discovery progress separately and
  provide a manifest/subtree option for genuinely small trials.
- Test the real Synology filesystem and WebDAV layer, not only a local temp
  directory.

This design provides deduplication and integrity checking, not backup. The
stated storage arrangement is RAID-0. RAID-0 provides no redundancy or backup
and must not be relied upon for data protection; loss of one member may lose the
array. This plan records that risk but does not invent a backup solution.

## Pixette, Synology indexing, and permissions

If the object store is beneath `/var/services/photo`, Pixette and archive scans
must exclude it. Visible parent directories need permissions allowing intended
Pixette/WebDAV operations; object files are immutable by policy, not by a
separate permission mode, because hardlinks share inode permissions.

All archive scans must prune both an in-tree object store and `@eaDir`.

The representative Oxygen trial must create or migrate a visible hardlink, let
Synology Photos/media indexing settle, recheck the object hash, and inspect
`@eaDir`, ACLs, ownership, modes, timestamps, and xattrs. It must also open and
rename the visible file through the WebDAV account Pixette uses, test whether
that account can modify file contents, and confirm Pixette excludes the object
store while displaying the visible photo. POSIX permissions alone do not prove
Synology/WebDAV/Pixette behavior.

## Transition from the ledger

`photos-oxygen-sha` remains the safety reference during migration and should not
be deleted until the object store has been verified and accepted. It is not the
long-term deduplication authority. A lightweight provenance catalogue may be
added later, but object content and visible hardlinks are authoritative.

Migration and ingest are separate from the existing laptop review/copy flow.
Do not replace the working ledger workflow wholesale until a representative
subset has been migrated, verified, and tested through Pixette/WebDAV.

## Implementation order

1. Add bounded selection (`--path`, `--limit`, deterministic ordering) and the
   configurable object root.
2. Add bounded progress, logging modes, retention, and final summaries.
3. Add manifest-producing dry-run and manifest-authoritative apply.
4. Add conservative resource controls, descriptor/stat race checks, locking,
   and existing-object verification.
5. Test migration on a small isolated directory and one real year subset,
   including Synology indexing, WebDAV, ACLs, and xattrs.
6. Implement `oxygen-verify`.
7. Implement `oxygen-ingest`, including cross-filesystem copy and collision
   behavior.
8. Migrate the remaining archive in monitored, no-write batches.
9. Consider garbage collection only after extended successful operation; it may
   remain unimplemented.

## Tests

Minimum tests:

- SHA-256 object fan-out and configurable same-filesystem object roots are
  correct.
- The object store, `@eaDir`, ledgers, `.DS_Store`, and symlinks are excluded.
- `--root`, `--path`, `--limit`, and deterministic ordering select the expected
  files without allowing paths outside the root.
- Dry-run performs no changes and emits a reviewable manifest.
- Apply uses exactly the reviewed manifest and rejects changed or mismatched
  entries.
- Duplicate visible files become hardlinks to one object.
- Re-running migration is a no-op.
- Changed/disappearing files fail safely without a mandatory second hash pass.
- Corrupt existing objects are not reused.
- Same-filesystem and cross-filesystem ingest publish only verified objects.
- Visible-name collisions follow the defined no-op/refuse rules.
- Verify detects pathname/content mismatches.
- Default logs remain bounded and retention limits are enforced.
- Resource limits do not start uncontrolled workers.
- Synology indexing, WebDAV, Pixette, ACL, xattr, and `@eaDir` behavior pass the
  representative real-system trial.

No daemon, database, remote SSH orchestration, or new dependency is needed for
this design.
