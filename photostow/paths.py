from __future__ import annotations

from pathlib import Path

ARCHIVE_ROOT = Path("/var/services/photo")


def canonical_archive_path(path: str | Path) -> str:
    """Return an archive path using the canonical Synology spelling."""
    value = str(path)
    archive_name = str(ARCHIVE_ROOT)
    if value == archive_name or value.startswith(archive_name + "/"):
        return value
    resolved = Path(value).resolve()
    archive = ARCHIVE_ROOT.resolve()
    if resolved == archive or archive in resolved.parents:
        return str(ARCHIVE_ROOT / resolved.relative_to(archive))
    return value


def archive_path_aliases(path: str | Path) -> set[str]:
    """Return persisted and resolved spellings accepted for an archive path."""
    value = str(path)
    return {value, str(Path(value).resolve()), canonical_archive_path(value)}
