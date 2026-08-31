# photostow plan

## Goal

Maintain the Synology photo archive as a content-addressed store while keeping the
existing year/filename tree as the user-facing and Pixette-indexed view.

The **Archive Root** is:

```text
/var/services/photo
```

The **Object Store** uses this layout:

```text
<Object Store>/sha256/ab/cdef...
```

The default production Object Store is `/volume1/photostow`; it need not be
inside the photo share. A location elsewhere on the same filesystem may better
avoid Synology Photos/media-indexing and `@eaDir` activity. Resolve the Archive
Root on Oxygen and test both placement and indexing behavior before choosing the
production location.

Each visible photo is a hardlink to its content object. SHA-256 is the identity;
filenames, dates, and directory names are not identity. Object contents must not
be modified after ingestion. This is an invariant and operating policy, not a
separate-permission guarantee, because hardlinks share an inode and permissions.

## Commands

Commands beginning with `oxygen-` run on Oxygen and use local filesystem
operations. They must not require SSH, `uv`, or third-party Python packages.
Oxygen has Python 3.9.14 installed, so the Oxygen entry points must remain
compatible with that runtime.

Planned commands:

```text
oxygen-migrate   existing visible tree -> Object Store
oxygen-ingest    incoming files -> Object Store and visible tree
oxygen-verify    check object names and contents
oxygen-gc        optionally remove unreferenced objects
```

`oxygen-migrate` applies changes by default. `--dry-run` is an explicit preview
mode. Migration halts on the first error by default; `--continue-on-error`
allows the run to process later files after recording the failure.

The existing laptop-side Photos workflow can remain separate while this is
introduced and tested.

## Migration

### End state

The end state is:

- The Object Store contains one immutable content object per SHA-256, stored
  as `sha256/ab/cdef...`.
- The Archive Root retains the existing visible names and directories, with
  each migrated file represented by a hardlink to its object.
- There must not be multiple visible hardlink references to the same content.
  The object link plus its one intended visible reference is required; two or
  more visible files referencing one object is an error.

The Object Store must be on the same filesystem as the Archive Root. Migration
must check this before making changes.

### One-file-at-a-time migration

The normal migration is a streaming apply, not a full dry-run followed by a
manifest apply. It processes files independently and reports each completed
file:

1. Walk the selected visible tree in deterministic order, excluding
   the Object Store, `@eaDir`, `._DAV`, `.afpDeleted*`, ledgers, `.DS_Store`,
   symlinks, and unreadable paths.
2. Open the file without following symlinks, hash it once, and confirm its
   device, inode, size, and modification time remain stable.
3. Derive the Object Store path `sha256/ab/cdef...` from the digest.
4. If the object exists and is the same inode as the source, treat the source
   as already migrated and skip it when the inode link count is exactly `2`:
   one object-store link plus one visible link. An inode link count of `3` or
   more is an error because it means the object has multiple references; add it
   to the fail list.
5. If the object does not exist and the source inode link count is `2` or more,
   treat it as an error and add it to the fail list. The other link may be a
   second visible file or an external reference, and migration must not guess.
   If the object exists but is a different inode, likewise do not create another
   visible reference; add the source to the fail list.
6. If the object path already exists for a different inode, do not rename or
   link the source. Add the source to a durable fail list for review or later
   deletion.
7. If the object path is absent and the source has one link, atomically rename
   the source into the object shard and create one hardlink at its original
   visible path. The rename and link must preserve the source inode and
   timestamps.
8. Continue only after the file operation succeeds. An unexpected condition
   fails the run with a nonzero status and a clear path-specific error.

Each completed file is a committed, idempotent unit. An interrupted run can be
rerun without a manifest and without damaging completed files. `--path`,
`--limit`, `--exclude`, `--nice`, bounded progress, and serial processing remain
available. The process must not load the complete file list or hash list before
starting work.

A dry-run may remain as an optional preview for a small selection, but the
manifest-producing and manifest-authoritative apply workflow is not the normal
migration path and should be removed if it is not needed after the streaming
path is proven.

### Failure list and safety

The fail list must include at least the source path, digest when known, object
path, reason, and timestamp. The default location is
`./migration-failures.jsonl` in the caller's working directory. It uses
append-safe JSON Lines records and must remain reviewable without being
mistaken for a successful migration record. Migration halts on the first error
by default; `--continue-on-error` records the error and continues with later
files.

If the digest-derived object already exists, migration must never overwrite it,
rename the source over it, or create another visible hardlink. The existing
object can be reviewed separately; the source can later be deleted only after
an explicit comparison and decision.

Unexpected errors, unstable files, missing files, directory errors, hardlink
failures, cross-filesystem stores, malformed object paths, and unsafe symlink
conditions must fail closed. A completed prior file remains valid, but the run
must stop rather than continue past an unexpected condition.

Only one Oxygen migration/ingest/GC operation may run at a time. Every publish
must create the object first or rename the source atomically, then create the
visible hardlink. No visible path may point to a partial object. The selected
tree must not be written by Pixette or users during migration.

### Metadata and timestamps

Moving the source inode into the Object Store and hardlinking its original
name must preserve its existing timestamps and inode metadata. Migration must
record or test this behavior on Oxygen, including permissions, ownership, ACLs,
xattrs, and WebDAV/Pixette behavior. If per-path metadata is required, stop
rather than silently adopting a hardlink representation that cannot preserve
it.

Content immutability remains an operating policy: writing through any visible
hardlink changes the object. Periodic `oxygen-verify` remains the full content
integrity check, not a prerequisite SHA pass for every already-linked file.

## Ingest

`oxygen-ingest` accepts incoming files and a target visible directory/year:

1. Hash each incoming file once using the same before/after descriptor checks as
   migration.
2. Look up the exact SHA-derived path in the Object Store.
3. If the object exists as a regular, non-symlink file with the same size,
   trust and reuse it without another full hash. Do not create another visible
   hardlink by default; report the incoming file as already represented and
   add it to the fail list for review.
4. If the object is absent, publish it using a race-safe same-filesystem
   hardlink or cross-filesystem verified temporary copy and atomic rename.
5. Once a new verified object exists, create the requested visible hardlink,
   preserving the requested filename.
6. Refuse in-place edits to existing objects and refuse conflicting visible
   destinations.

A size mismatch, malformed object, symlink, or race is an error. An explicit
safe-verification mode may hash an existing object before reuse when required.
Periodic `oxygen-verify` remains the full integrity check.

Visible filename collision behavior is explicit:

```text
target absent                         create it
target exists with the same content   no-op
target exists with different content  refuse by default
incoming content already has a visible reference  fail-list; do not add one
```

Ingest must never silently overwrite or rename an existing visible file. It
must use the same progress, resource-limit, locking, and race-safety rules as
migration.

The laptop-side planner is responsible for choosing the visible destination;
Oxygen ingest receives an explicit destination and must not query EXIF data.
The normal destination is `YYYY/original-filename`, using the capture date from
EXIF. Missing or invalid dates go to an explicit `undated/` review area.
Destination collisions are deterministic: the same destination and digest is a
no-op; the same destination with a different digest is refused; a digest-based
suffix may be used only by the planner and only when the review policy permits
it.

### Laptop transfer planning

The Object Store supplies a sorted digest inventory for the laptop. The laptop
keeps a separate incremental hash cache with the relative path, device, inode,
size, modification time, and SHA-256. Unchanged fingerprints reuse the cached
hash; changed or new files are hashed. The cache is an optimization, not an
authority, and a full rehash remains available for periodic verification.

Hashing and transfer may overlap. As each file's digest becomes known, compare
it with the Oxygen digest set and queue only missing digests. Maintain an
in-memory claimed-digest set so duplicate laptop files queue once, while a
duplicate report records every duplicate group and processing continues. A
completed run reports duplicates with a non-zero status. The transfer manifest
must retain source and intended destination paths even though digest membership
uses hashes only, and it must be durable enough to resume after interruption.

### Object-reference authority

Ingest must determine whether content is already present by deriving the
canonical object path from the source SHA-256:

```text
<Object Store>/sha256/<first two hex>/<remaining 62 hex>
```

The object filename is the reference key. It must not consult
`photos-oxygen-sha` or any path-based ledger. After hashing the incoming file,
ingest must inspect the exact SHA-derived object path. The normal fast path is:

- an existing regular, non-symlink object with the incoming file's size is
  trusted and reused without hashing the object again;
- a missing object is created using the existing same-filesystem hardlink or
  cross-filesystem verified-copy path;
- an existing object with a different size is not reused and must be reported
  for safe repair or explicit full verification.

This deliberately treats size as a cheap consistency check, not proof of
integrity. The operational assumption is that corruption is rare and periodic
`oxygen-verify` supplies the full integrity check. An explicit safe-verification
mode may hash an existing object before reuse when required.

If the requested visible destination is absent, ingest creates exactly one
additional hardlink to the object. That additional link is the intended visible
reference, not a duplicate object. If the destination already names the same
content, ingest is a no-op; a different content at that name is refused.

The object file's link count is the reference count: one link for the object
itself plus one for the intended visible hardlink. Add a small
reference-reporting operation that lists visible paths sharing each object
inode, excluding `@eaDir`, metadata, and the Object Store. Use it to review
unexpected references before removing a visible path. Removing a visible
hardlink must never remove the object; an object with only its object-store link
becomes an `oxygen-gc` candidate.

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
- Limit retained logs and fail lists by both count and total size; for example,
  retain at most the newest 10 completed runs and 10 MiB total. Failed-run
  retention may be longer but must remain bounded and configurable. Never
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
  provide a subtree option for genuinely small trials.
- Test the real Synology filesystem and WebDAV layer, not only a local temp
  directory.

This design provides deduplication and integrity checking, not backup. The
stated storage arrangement is RAID-0. RAID-0 provides no redundancy or backup
and must not be relied upon for data protection; loss of one member may lose the
array. This plan records that risk but does not invent a backup solution.

## Pixette, Synology indexing, and permissions

If the Object Store is beneath the Archive Root, Pixette and archive scans
must exclude it. Visible parent directories need permissions allowing intended
Pixette/WebDAV operations; object files are immutable by policy, not by a
separate permission mode, because hardlinks share inode permissions.

All archive scans must prune the Object Store, `@eaDir`, and `._DAV`, and exclude `.afpDeleted*` files.

The representative Oxygen trial must create or migrate a visible hardlink, let
Synology Photos/media indexing settle, recheck the object hash, and inspect
`@eaDir`, ACLs, ownership, modes, timestamps, and xattrs. It must also open and
rename the visible file through the WebDAV account Pixette uses, test whether
that account can modify file contents, and confirm Pixette excludes the object
store while displaying the visible photo. POSIX permissions alone do not prove
Synology/WebDAV/Pixette behavior.

## Transition from the ledger

`photos-oxygen-sha` remains the safety reference during migration and should not
be deleted until the Object Store has been verified and accepted. It is not the
long-term deduplication authority. A lightweight provenance catalogue may be
added later, but object content and visible hardlinks are authoritative.

Migration and ingest are separate from the existing laptop review/copy flow.
Do not replace the working ledger workflow wholesale until a representative
subset has been migrated, verified, and tested through Pixette/WebDAV.

Once that acceptance is complete and reference-based ingest is proven:

1. Remove the Makefile targets and CLI/library code for updating, pruning, and
   installing `photos-oxygen-sha` as an Oxygen archive authority. Keep a
   portable digest inventory for laptop-side transfer decisions.
2. Remove `audit-new-remote` and `scripts/oxygen-new-hash-audit.sh`; streaming
   migration and object filename lookup replace their new-path audit.
3. Remove ledger-based `duplicate-groups` and `delete-duplicates`; replace
   them with the object-reference report and explicit deletion of unwanted
   visible hardlinks.
4. Replace direct `copy-tree`/`archive-reviewed` publication with reviewed
   batch ingest once Oxygen ingest accepts the reviewed input flow. Until then,
   retain the laptop review stage and transfer path.
5. Add an Oxygen command to export a sorted, newline-delimited list of valid
   SHA-256 object names for laptop comparison. The export is derived from
   Object Store, contains digests rather than Archive Root paths, and is a
   transfer snapshot rather than a second archive authority.
6. Add the laptop incremental hash cache and resumable planner/transfer flow;
   overlap hashing with transfer, report duplicate groups while continuing, and
   queue only one representative per digest.
7. Update README, Makefile, tests, and operational instructions to use object
   references and the digest inventory instead of the path ledger.

Until then, retain the ledger workflows for rollback, laptop
`library-missing`, and comparison of migrated versus unmigrated files.

## Implementation order

1. Implement streaming, one-file-at-a-time migration with atomic publish,
   deterministic selection, bounded progress, fail-list persistence, and the
   configurable object root.
2. Implement the size-check ingest fast path and collision/fail-list behavior;
   keep full existing-object hashing behind an explicit safe-verification mode.
3. Remove manifest generation, manifest-authoritative apply, and their tests;
   retain only a small optional dry-run preview if it remains useful.
4. Test migration and ingest on an isolated directory and one real year,
   including timestamps, hardlink counts, Synology indexing, WebDAV, ACLs,
   xattrs, interruption, and rerun recovery.
5. Implement `oxygen-verify` as the periodic full integrity check and add the
   visible-reference report.
6. Add a sorted digest-inventory export for laptop-side missing-file checks;
   do not transfer or maintain the old path-based archive ledger for this
   purpose.
7. Add the laptop incremental hash cache and resumable transfer planner;
   duplicate groups are reported while processing continues and only one
   representative per digest is queued.
8. Migrate the remaining archive in monitored, no-write batches.
9. Retire the ledger, audit, duplicate, and direct-copy workflows listed above
   only after streaming migration, ingest, digest export, and production
   acceptance checks pass.
10. Consider garbage collection only after extended successful operation; it
    may remain unimplemented.

## Tests

Minimum tests:

- SHA-256 object fan-out and configurable same-filesystem object roots are
  correct.
- The Object Store, `@eaDir`, ledgers, `.DS_Store`, and symlinks are excluded.
- `--root`, `--path`, `--limit`, and deterministic ordering select the expected
  files without allowing paths outside the root.
- Streaming migration commits each file independently and resumes safely after
  interruption without a manifest.
- A source with multiple visible references is an error and enters the fail
  list; an already-migrated source with exactly one visible reference is skipped.
- An existing SHA-derived object is reused by size fast path without a second
  hash by default; safe mode verifies its contents.
- Existing SHA-derived objects cause duplicate incoming references to enter the
  fail list rather than creating another visible hardlink.
- Re-running migration is a no-op.
- Changed/disappearing files fail safely without a mandatory second hash pass.
- Corrupt existing objects are not reused.
- Same-filesystem and cross-filesystem ingest publish only verified objects.
- Ingest reuses an existing object using the SHA-derived filename and size
  fast path, without a second full hash by default.
- Safe verification can require the existing object's content hash before reuse.
- Ingest does not read or update the path ledger.
- Digest export contains only valid object-name hashes and is suitable for
  transfer to the laptop.
- The reference report groups visible paths by object inode and excludes
  metadata and object-store paths.
- Visible-name collisions follow the defined no-op/refuse rules.
- Verify detects pathname/content mismatches.
- Default logs remain bounded and retention limits are enforced.
- Resource limits do not start uncontrolled workers.
- Synology indexing, WebDAV, Pixette, ACL, xattr, and `@eaDir` behavior pass the
  representative real-system trial.

No daemon, database, remote SSH orchestration, or new dependency is needed for
this design.
