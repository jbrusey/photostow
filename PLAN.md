# photostow plan

## Goal

Archive exported Photos.app images from several laptops and memory sticks to Synology without trusting filenames or "last archived" dates.

## Lazy design

Use content hashes as the archive truth:

1. Hash original files inside the local Photos library.
2. Compare those hashes with the Synology hash ledger.
3. Use the unmatched local originals to choose the export/import set.
4. Export or copy only those unmatched photos.
5. Refresh the Synology ledger after import.

Dates are only for narrowing the export/search window and optional folder naming. They are not used for correctness.

## Repository shape

```text
photostow/
  photostow/          # small stdlib Python package
  tests/              # pytest tests
  Makefile            # uv commands
  pyproject.toml      # uv/setuptools config
```

## First commands

```sh
uv sync
make test

# Hash a Photos library or export folder from any laptop
uv run photostow hash "$HOME/Pictures/Photos Library.photoslibrary/originals" > local.sha256

# Find local originals that are not already on Synology
uv run photostow missing local.sha256 photos-oxygen-sha > missing.txt
```

## Synology workflow

Keep a shared archive ledger, e.g. `photos-oxygen-sha`, copied down before each run or generated on oxygen:

```sh
ssh oxygen "find /var/services/photo -type f -not -path '*/@eaDir/*' -print0 | xargs -0 sha256sum" > photos-oxygen-sha
```

For large archives, keep the existing incremental Makefile idea: reuse old hashes by filename and hash only new paths. Full archive scans are slow.

## Photos library workflow

Start with the library files themselves, not a manual export:

1. Find local Photos originals under `Photos Library.photoslibrary/originals`.
2. Hash them and compare against the Synology ledger.
3. For unmatched originals, compute the earliest datestamp available from:
   - EXIF `CreateDate` if available later via `exiftool`/`osxphotos`, or
   - file modification time as the no-dependency fallback.
4. Use that earliest date as the Photos.app export start date if manual export is still needed.

Avoiding manual export:

- For original camera files, copy the unmatched library originals directly.
- For adjusted/edited versions, prefer `osxphotos export` if we need rendered edits; Photos.app has no good built-in CLI export.
- Do not read Photos' private SQLite database directly unless `osxphotos` is not enough.

## Multi-laptop rule

Each laptop can run the same tool locally. The only shared state is the Synology hash ledger. If two laptops archive the same photo, the second run sees the same SHA-256 and skips it.

## Tests

Initial test coverage:

- SHA-256 hashing is stable.
- hash ledger parser handles spaces in paths.
- missing-file detection compares hashes, not names or dates.
- earliest datestamp is computed only from unmatched local files.

No database, daemon, config format, or private Photos DB parsing until needed.
