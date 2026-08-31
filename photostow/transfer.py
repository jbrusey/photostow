from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from photostow.core import iter_files, sha256_file

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class HashedFile:
    path: Path
    digest: str


@dataclass(frozen=True)
class TransferPlan:
    files: tuple[HashedFile, ...]
    duplicates: dict[str, tuple[Path, ...]]


def _fingerprint(path: Path) -> dict[str, int]:
    info = path.stat(follow_symlinks=False)
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
    }


def scan_cached(root: Path, cache: Path) -> list[HashedFile]:
    if cache.is_symlink() or any(parent.is_symlink() for parent in cache.parents):
        raise ValueError(f"unsafe hash cache path: {cache}")
    old: dict[str, dict[str, object]] = {}
    if cache.exists():
        with cache.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("version") != 1 or not isinstance(data.get("files"), dict):
            raise ValueError(f"unsupported hash cache: {cache}")
        old = data["files"]

    records: dict[str, dict[str, object]] = {}
    result: list[HashedFile] = []
    for path in iter_files(root):
        relative = str(path.relative_to(root))
        fingerprint = _fingerprint(path)
        previous = old.get(relative)
        digest = (
            previous.get("sha256")
            if previous and previous.get("fingerprint") == fingerprint
            else None
        )
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            digest = sha256_file(path)
        records[relative] = {"fingerprint": fingerprint, "sha256": digest}
        result.append(HashedFile(path, digest))

    cache.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(dir=cache.parent, prefix=f".{cache.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "files": records}, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, cache)
    except BaseException:
        os.unlink(temporary_name)
        raise
    return result


def plan_missing(files: list[HashedFile], oxygen_digests: set[str]) -> TransferPlan:
    by_digest: dict[str, list[Path]] = {}
    for record in files:
        by_digest.setdefault(record.digest, []).append(record.path)
    duplicates = {
        digest: tuple(sorted(paths))
        for digest, paths in by_digest.items()
        if len(paths) > 1
    }
    selected = [
        HashedFile(min(paths), digest)
        for digest, paths in sorted(by_digest.items())
        if digest not in oxygen_digests
    ]
    return TransferPlan(tuple(selected), duplicates)
