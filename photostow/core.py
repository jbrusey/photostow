from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterable, Iterator
from pathlib import Path


def iter_files(root: Path) -> Iterator[Path]:
    if root.is_symlink():
        raise ValueError(f"refusing symlink tree root: {root}")
    if not root.is_dir():
        raise NotADirectoryError(root)
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            yield path


def sha256_file(path: Path) -> str:
    if path.is_symlink():
        raise ValueError(f"refusing symlink hash input: {path}")
    if not path.parent.is_dir():
        raise ValueError(f"hash input parent is not a directory: {path.parent}")
    if not path.exists():
        raise ValueError(f"hash input does not exist: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(f"refusing symlink hash input parent: {parent}")
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe no-follow file opening is unavailable")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        os.close(fd)
        raise ValueError(f"refusing non-regular hash input: {path}")
    with os.fdopen(fd, "rb") as f:
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
        after = os.fstat(f.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise ValueError(f"hash input changed while reading: {path}")
    try:
        named = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ValueError(f"hash input changed while reading: {path}") from error
    if (named.st_dev, named.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError(f"hash input changed while reading: {path}")
    return h.hexdigest()


def hash_tree(root: Path) -> Iterator[tuple[str, Path]]:
    for path in iter_files(root):
        yield sha256_file(path), path


def parse_sha_lines(lines: Iterable[str]) -> Iterator[tuple[str, str]]:
    for line in lines:
        line = line.rstrip("\n")
        if not line:
            continue
        digest, _, name = line.partition("  ")
        if not name:
            digest, _, name = line.partition(" ")
        if digest and name:
            yield digest, name


def missing_hash_records(
    source_lines: Iterable[str], archive_lines: Iterable[str]
) -> Iterator[tuple[str, str]]:
    archived = {digest for digest, _ in parse_sha_lines(archive_lines)}
    for digest, name in parse_sha_lines(source_lines):
        if digest not in archived:
            yield digest, name


def ledger_paths(lines: Iterable[str]) -> set[str]:
    return {name for _, name in parse_sha_lines(lines)}


def paths_not_in_ledger(
    current_paths: Iterable[str], ledger_lines: Iterable[str]
) -> list[str]:
    known = ledger_paths(ledger_lines)
    return [path for path in sorted(current_paths) if path not in known]


def prune_ledger_lines(
    ledger_lines: Iterable[str], current_paths: set[str]
) -> list[str]:
    rows = []
    seen = set()
    for digest, path in parse_sha_lines(ledger_lines):
        if path in current_paths and path not in seen:
            rows.append(f"{digest}  {path}")
            seen.add(path)
    return rows
