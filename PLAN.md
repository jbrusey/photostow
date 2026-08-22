# photostow plan

## Goal

Maintain the Synology photo archive as a content-addressed store while keeping the
existing year/filename tree as the user-facing and Pixette-indexed view.

The archive root is:

```text
/var/services/photo
```

The object store is:

```text
/var/services/photo/.objects/sha256/ab/cdef...
```

Each visible photo is a hardlink to its immutable content object. SHA-256 is the
identity; filenames, dates, and directory names are not identity.

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

1. Exclude `.objects`, `@eaDir`, ledgers, `.DS_Store`, symlinks, and unreadable
   paths.
2. Select files deterministically in sorted path order.
3. Hash selected files and map each digest to `.objects/sha256/ab/cdef...`.
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
oxygen-migrate /var/services/photo/2006 --dry-run --limit 10
oxygen-migrate /var/services/photo/2006 --dry-run --path IMG_1234.JPG
oxygen-migrate /var/services/photo/2006 --dry-run --path 08/15
```

Required selection options:

- `--limit N`: process at most N files after deterministic selection.
- `--path PATH`: restrict processing to a path or subtree under the root.
- `--exclude PATTERN`: repeatable exclusion for an additional subtree/pattern.

An apply run must use the same selection options as its reviewed dry-run.

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

For a source file, hash and link operations must not silently turn a changed
file into an object under the old digest. A failed file should be reported with
its path and the run should exit nonzero after processing the selected batch,
without deleting unverified data.

Existing objects must be verified before reuse, at least in migration's safe
mode. A later `oxygen-verify` provides periodic full integrity checking.

### Concurrency and recovery

Only one Oxygen migration/ingest/GC operation may run at a time. Use a local
lock and release it on interruption. Each file operation should be atomic from
the visible tree's perspective: create the object first, create a temporary
hardlink in the destination directory, then replace the visible path. An
interrupted run may leave an extra object, but must not leave a visible path
pointing at a partial object.

The dry-run and apply phases should support a manifest. A reviewed manifest
records the selected path, size, and digest; apply can refuse to use a changed
source unless explicitly forced. This prevents a long dry-run from silently
applying to a different set of files.

### Metadata and hardlink limitations

Hardlinks share inode metadata. Replacing duplicate visible files can therefore
change their permissions, timestamps, ownership, ACLs, and extended attributes
to those of the object inode. This must be documented and tested on Oxygen.
If per-path metadata is required by Pixette or WebDAV, hardlinks are not a
sufficient representation and the design must stop rather than silently lose
it.

Content immutability is a safety rule, not a guarantee: any account with write
permission to a visible hardlink can modify the shared inode and corrupt the
object. Before apply, define how object writes are prevented or detected
(permissions/ACLs, application policy, or frequent verification). Do not claim
objects are immutable until that mechanism is tested.

## Ingest

`oxygen-ingest` accepts incoming files and a target visible directory/year:

1. Hash each incoming file.
2. Verify or create the corresponding object.
3. Create the requested visible hardlink, preserving the requested filename.
4. Never copy duplicate object bytes unnecessarily.
5. Refuse in-place edits to existing objects.

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
remove objects with no visible hardlinks, but only after a report/dry-run and a
second verification pass. Objects with unexpected link counts must be retained
and reported.

## Operational confidence

Every run must make its state obvious without inspecting the filesystem:

- Print the command mode, root, selection options, and start time immediately.
- Report discovery, selection, hashing, object creation/reuse, relinking,
  skipped files, errors, elapsed time, rate, and the last path.
- Print progress on a time interval as well as a file interval, with flushed
  output; do not wait until the final hash completes.
- Write an optional durable log and manifest so an SSH disconnect does not
  destroy the evidence of what happened.
- End with a machine-readable exit status and a human summary. Zero means every
  selected file was handled and verified; nonzero means review the error list.
- Make Ctrl-C report whether the run stopped cleanly and what remains.

A small real-data trial is successful only when the user can compare before and
after counts, run `oxygen-verify`, confirm expected hardlink counts, and open,
rename, and write-test a migrated visible file through the same WebDAV account
Pixette uses. POSIX permissions alone do not prove WebDAV/Pixette behavior.

## Other risks and invariants

- Check free bytes, free inodes, and hardlink support before apply.
- Keep the object namespace versioned by algorithm (`sha256`) and reject
  malformed object names.
- Treat corrupt, partial, orphaned, and unexpected object entries as reportable
  states; never silently reuse them.
- Protect against changes while hashing by using file identity/stat checks and
  a final hash check; if the archive is actively changing, offer a maintenance
  window or retry policy.
- Define how case-sensitive names, Unicode names, long paths, and unusual
  filenames behave before ingest.
- Do not assume a sorted `--limit` is cheap: selection may require a full
  directory walk before hashing. Report discovery progress separately and
  provide a manifest/subtree option for genuinely small trials.
- Test the real Synology filesystem and WebDAV layer, not only a local temp
  directory.

## Pixette and permissions

Pixette must exclude `.objects` from indexing. Visible parent directories need
permissions allowing Pixette to rename files; object files are immutable by
policy, not by a separate permission mode, because hardlinks share inode
permissions.

All archive scans must prune both `.objects` and `@eaDir`.

## Transition from the ledger

`photos-oxygen-sha` remains the safety reference during migration and should not
be deleted until the object store has been verified and accepted. It is not the
long-term deduplication authority. A lightweight provenance catalogue may be
added later, but object content and visible hardlinks are authoritative.

Migration and ingest are separate from the existing laptop review/copy flow.
Do not replace the working ledger workflow wholesale until a representative
subset has been migrated, verified, and tested through Pixette/WebDAV.

## Implementation order

1. Add bounded selection (`--path`, `--limit`, deterministic ordering).
2. Add immediate progress and final summaries.
3. Add conservative CPU/resource controls.
4. Add race and existing-object verification.
5. Test migration on a small isolated directory and one real year subset.
6. Implement `oxygen-verify`.
7. Implement `oxygen-ingest`.
8. Define and implement garbage collection.
9. Migrate the remaining archive in monitored batches.

## Tests

Minimum tests:

- SHA-256 object fan-out is correct.
- `.objects`, `@eaDir`, ledgers, `.DS_Store`, and symlinks are excluded.
- `--path`, `--limit`, and deterministic ordering select the expected files.
- Dry-run performs no changes and emits progress/final summaries.
- Duplicate visible files become hardlinks to one object.
- Re-running migration is a no-op.
- Changed/disappearing files fail safely.
- Corrupt existing objects are not reused.
- Verify detects pathname/content mismatches.
- Resource limits do not start uncontrolled workers.

No daemon, database, remote SSH orchestration, or new dependency is needed for
this design.
