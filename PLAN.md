# photostow plan

## Goal

Archive exported Photos.app images from several laptops and memory sticks to Synology without trusting filenames or "last archived" dates.

## Lazy design

Use content hashes as the archive truth:

1. Export from Photos.app to a local staging folder.
2. Hash files in the staging folder.
3. Compare those hashes with the Synology hash ledger.
4. Copy only files whose SHA-256 is not already archived.
5. Refresh the Synology ledger after import.

Dates are only for choosing/exporting batches and optional folder naming. They are not used for correctness.

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

# Hash an export folder from any laptop
uv run photostow hash /path/to/export > staging.sha256

# Find files in staging that are not already on Synology
uv run photostow missing staging.sha256 photos-oxygen-sha > missing.txt
```

## Synology workflow

Keep a shared archive ledger, e.g. `photos-oxygen-sha`, copied down before each run or generated on oxygen:

```sh
ssh oxygen "find /var/services/photo -type f -not -path '*/@eaDir/*' -print0 | xargs -0 sha256sum" > photos-oxygen-sha
```

For large archives, keep the existing incremental Makefile idea: reuse old hashes by filename and hash only new paths. Full archive scans are slow.

## Multi-laptop rule

Each laptop can run the same tool locally. The only shared state is the Synology hash ledger. If two laptops archive the same photo, the second run sees the same SHA-256 and skips it.

## Tests

Initial test coverage:

- SHA-256 hashing is stable.
- hash ledger parser handles spaces in paths.
- missing-file detection compares hashes, not names or dates.

No database, daemon, config format, or Photos library parsing until needed.
