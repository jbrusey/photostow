# photostow

Small photo archive helper: inspect a Photos.app library read-only, hash local originals, compare with the Synology hash ledger, and identify unseen content.

```sh
uv sync
make check
scripts/oxygen-new-hash-audit.sh oxygen /var/services/photo photos-oxygen-sha
uv run photostow update-remote-ledger oxygen /var/services/photo photos-oxygen-sha
uv run photostow inspect-library "$HOME/Pictures/Photos Library.photoslibrary"
uv run photostow library-missing "$HOME/Pictures/Photos Library.photoslibrary" photos-oxygen-sha > missing.tsv
uv run photostow stage-review missing.tsv "$HOME/Pictures/Photos Library.photoslibrary/originals" review
# inspect/delete unwanted files in review/ with Finder
uv run photostow copy-tree review oxygen /var/services/photo
```

See [PLAN.md](PLAN.md).
