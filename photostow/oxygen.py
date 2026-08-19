from __future__ import annotations

import os
import tempfile
from pathlib import Path

from photostow.core import sha256_file


def visible_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in {"@eaDir", ".objects"}]
        files.extend(Path(directory) / name for name in names)
    return sorted(
        path
        for path in files
        if not path.name.startswith("photos-oxygen-sha") and path.name != ".DS_Store"
    )


def object_path(root: Path, digest: str) -> Path:
    return root / ".objects" / "sha256" / digest[:2] / digest[2:]


def migrate(root: Path, dry_run: bool = True) -> int:
    root = root.resolve()
    records = [(sha256_file(path), path) for path in visible_files(root)]
    for digest, path in records:
        obj = object_path(root, digest)
        if dry_run:
            action = "link" if obj.exists() else "create"
            print(f"{action} {path} -> {obj}")
            continue
        obj.parent.mkdir(parents=True, exist_ok=True)
        if not obj.exists():
            os.link(path, obj)
        elif not os.path.samefile(path, obj):
            fd, name = tempfile.mkstemp(dir=path.parent)
            os.close(fd)
            replacement = Path(name)
            replacement.unlink()
            try:
                os.link(obj, replacement)
                os.replace(replacement, path)
            finally:
                replacement.unlink(missing_ok=True)
    return len(records)
