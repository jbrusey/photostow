# photostow

Small photo archive helper: inspect a Photos.app library read-only, hash local originals, compare with the Synology hash ledger, and identify unseen content.

```sh
uv sync
make check
uv run photostow inspect-library "$HOME/Pictures/Photos Library.photoslibrary"
uv run photostow library-missing "$HOME/Pictures/Photos Library.photoslibrary" photos-oxygen-sha > missing.tsv
```

See [PLAN.md](PLAN.md).
