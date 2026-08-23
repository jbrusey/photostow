# Assumptions

- Local development and CI do not have the production Oxygen/Synology filesystem,
  Synology indexing, or the Pixette WebDAV account available.
- `make review` is therefore the current verified gate for code changes.
- No production migration or deletion is considered approved until the unchecked
  real-Oxygen items in `TECHNICAL_REVIEW.md` are completed and recorded.
- The canonical production archive path remains `/var/services/photo`.
- Remote scanner output is assumed to be UTF-8; non-UTF-8 filenames require production acceptance testing before support is claimed.
- Remote hashing is assumed to provide GNU-compatible `sha256sum --zero`; local GNU coreutils validation does not establish Synology compatibility.
