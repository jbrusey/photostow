from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path


def iter_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
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


def paths_not_in_ledger(current_paths: Iterable[str], ledger_lines: Iterable[str]) -> list[str]:
    known = ledger_paths(ledger_lines)
    return [path for path in sorted(current_paths) if path not in known]
