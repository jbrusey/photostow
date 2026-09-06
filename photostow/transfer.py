from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from photostow.core import iter_files, sha256_file
from photostow.remote import SSH

RSYNC = shutil.which("rsync") or "rsync"

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


def completed_transfers(state: Path) -> set[tuple[str, str]]:
    if state.is_symlink() or any(parent.is_symlink() for parent in state.parents):
        raise ValueError(f"unsafe transfer state path: {state}")
    completed: set[tuple[str, str]] = set()
    if not state.exists():
        return completed
    with state.open(encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("status") != "complete":
                continue
            digest = record.get("digest")
            destination = record.get("destination")
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ValueError(f"invalid transfer state digest: {digest}")
            if not isinstance(destination, str) or not destination:
                raise ValueError("invalid transfer state destination")
            completed.add((digest, destination))
    return completed


def record_transfer(
    state: Path,
    digest: str,
    source: Path,
    destination: Path,
    status: str = "complete",
    error: str | None = None,
) -> None:
    if not _DIGEST.fullmatch(digest):
        raise ValueError(f"invalid transfer digest: {digest}")
    if status not in {"complete", "failed"}:
        raise ValueError(f"invalid transfer status: {status}")
    if state.is_symlink() or any(parent.is_symlink() for parent in state.parents):
        raise ValueError(f"unsafe transfer state path: {state}")
    state.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "digest": digest,
        "source": str(source),
        "destination": str(destination),
        "status": status,
    }
    if error is not None:
        record["error"] = error
    with state.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def read_transfer_manifest(path: Path) -> Iterator[HashedFile]:
    stream = sys.stdin if str(path) == "-" else path.open(encoding="utf-8")
    try:
        if next(stream, "").rstrip("\n") != "sha256\tpath\tdestination":
            raise ValueError("transfer manifest has an invalid header")
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 3:
                raise ValueError("transfer manifest row must have three columns")
            digest, source, destination = fields
            if not _DIGEST.fullmatch(digest):
                raise ValueError(f"invalid transfer digest: {digest}")
            destination_path = Path(destination)
            if destination_path.is_absolute() or ".." in destination_path.parts:
                raise ValueError(f"transfer destination escapes root: {destination}")
            yield HashedFile(Path(source), digest, destination_path)
    finally:
        if stream is not sys.stdin:
            stream.close()


def transfer_batch(
    files: list[HashedFile],
    source_root: Path,
    host: str,
    staging_root: str,
    oxygen_root: str,
    object_root: str,
    state: Path,
    completed: set[tuple[str, str]],
) -> int:
    pending = [
        record
        for record in files
        if record.destination is not None
        and (record.digest, str(record.destination)) not in completed
    ]
    if not pending:
        return 0
    relative: list[str] = []
    for record in pending:
        try:
            relative.append(str(record.path.relative_to(source_root)))
        except ValueError as error:
            raise ValueError(
                f"transfer source is outside root: {record.path}"
            ) from error
    payload = "".join(path + "\0" for path in relative).encode()
    remote_staging = f"{host}:{shlex.quote(staging_root.rstrip('/') + '/')}"
    try:
        subprocess.run(
            [
                RSYNC,
                "-a",
                "--from0",
                "--files-from=-",
                source_root.as_posix() + "/",
                remote_staging,
            ],
            input=payload,
            check=True,
        )
    except subprocess.CalledProcessError as error:
        for record in pending:
            destination = record.destination
            assert destination is not None
            record_transfer(
                state,
                record.digest,
                record.path,
                destination,
                "failed",
                str(error),
            )
        return 0
    completed_count = 0
    for record, source_relative in zip(pending, relative):
        destination = record.destination
        assert destination is not None
        remote_source = posixpath.join(staging_root, source_relative)
        command = shlex.join(
            [
                "oxygen-ingest",
                remote_source,
                destination.as_posix(),
                "--root",
                oxygen_root,
                "--object-root",
                object_root,
                "--safe-verify",
            ]
        )
        try:
            subprocess.run([*SSH, host, command], check=True)
        except subprocess.CalledProcessError as error:
            record_transfer(
                state,
                record.digest,
                record.path,
                destination,
                "failed",
                str(error),
            )
            continue
        record_transfer(state, record.digest, record.path, destination)
        completed.add((record.digest, str(destination)))
        completed_count += 1
    return completed_count


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
    count = 0
    for path in iter_files(root):
        if path.name == ".DS_Store":
            continue
        count += 1
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
