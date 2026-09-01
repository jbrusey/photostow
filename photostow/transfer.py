from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from photostow.core import iter_files, sha256_file

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class HashedFile:
    path: Path
    digest: str
    destination: Path | None = None


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


def _write_cache(cache: Path, records: dict[str, dict[str, object]]) -> None:
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


def iter_cached(root: Path, cache: Path, checkpoint: int = 100) -> Iterator[HashedFile]:
    if cache.is_symlink() or any(parent.is_symlink() for parent in cache.parents):
        raise ValueError(f"unsafe hash cache path: {cache}")
    old: dict[str, dict[str, object]] = {}
    if cache.exists():
        with cache.open(encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("version") != 1 or not isinstance(data.get("files"), dict):
            raise ValueError(f"unsupported hash cache: {cache}")
        old = data["files"]

    if type(checkpoint) is not int or checkpoint < 1:
        raise ValueError("checkpoint must be a positive integer")
    old_by_fingerprint: dict[tuple[tuple[str, object], ...], str] = {}
    for record in old.values():
        fingerprint = record.get("fingerprint")
        digest = record.get("sha256")
        if isinstance(fingerprint, dict) and isinstance(digest, str):
            old_by_fingerprint[tuple(sorted(fingerprint.items()))] = digest
    records: dict[str, dict[str, object]] = {}
    for count, path in enumerate(iter_files(root), 1):
        relative = str(path.relative_to(root))
        fingerprint = _fingerprint(path)
        previous = old.get(relative)
        digest = (
            previous.get("sha256")
            if previous and previous.get("fingerprint") == fingerprint
            else old_by_fingerprint.get(tuple(sorted(fingerprint.items())))
        )
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            digest = sha256_file(path)
        records[relative] = {"fingerprint": fingerprint, "sha256": digest}
        if count % checkpoint == 0:
            _write_cache(cache, records)
        yield HashedFile(path, digest)
    _write_cache(cache, records)


def scan_cached(root: Path, cache: Path) -> list[HashedFile]:
    return list(iter_cached(root, cache))


def plan_missing(
    files: list[HashedFile],
    oxygen_digests: set[str],
    destinations: dict[Path, Path] | None = None,
) -> TransferPlan:
    by_digest: dict[str, list[Path]] = {}
    for record in files:
        by_digest.setdefault(record.digest, []).append(record.path)
    duplicates = {
        digest: tuple(sorted(paths))
        for digest, paths in by_digest.items()
        if len(paths) > 1
    }
    selected = [
        HashedFile(
            min(paths), digest, destinations.get(min(paths)) if destinations else None
        )
        for digest, paths in sorted(by_digest.items())
        if digest not in oxygen_digests
    ]
    return TransferPlan(tuple(selected), duplicates)
