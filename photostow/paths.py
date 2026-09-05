from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ARCHIVE_ROOT = Path("/var/services/photo")


@lru_cache(maxsize=4)
def _resolved_archive_name(name: str) -> str:
    return str(Path(name).resolve())


def canonical_archive_path(path: str | Path) -> str:
    """Return an archive path using the canonical Synology spelling."""
    value = str(path)
    archive_name = str(ARCHIVE_ROOT)
    if value == archive_name or value.startswith(archive_name + "/"):
        return value
    resolved_archive = _resolved_archive_name(archive_name)
    if value == resolved_archive or value.startswith(resolved_archive + "/"):
        return archive_name + value[len(resolved_archive) :]
    resolved = Path(value).resolve()
    archive = Path(resolved_archive)
    if resolved == archive or archive in resolved.parents:
        return str(ARCHIVE_ROOT / resolved.relative_to(archive))
    return value


def archive_path_aliases(path: str | Path) -> set[str]:
    """Return persisted and resolved spellings accepted for an archive path."""
    value = str(path)
    canonical = canonical_archive_path(value)
    aliases = {value, canonical}
    if canonical == str(ARCHIVE_ROOT) or canonical.startswith(str(ARCHIVE_ROOT) + "/"):
        resolved_archive = _resolved_archive_name(str(ARCHIVE_ROOT))
        aliases.add(resolved_archive + canonical[len(str(ARCHIVE_ROOT)) :])
    else:
        aliases.add(str(Path(value).resolve()))
    return aliases
