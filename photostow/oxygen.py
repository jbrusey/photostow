from __future__ import annotations

import fcntl
import fnmatch
import gzip
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from photostow.core import parse_sha_lines
from photostow.paths import canonical_archive_path

DEFAULT_ARCHIVE_ROOT = Path("/var/services/photo")
DEFAULT_LEDGER = Path("/volume1/photostow/ledger/photos-oxygen-sha")


def iter_visible_files(
    root: Path,
    selected: Path | None = None,
    progress: bool = False,
    verbose: bool = False,
    exclude: tuple[str, ...] = (),
    excluded_root: Path | None = None,
) -> Iterator[Path]:
    if type(progress) is not bool or type(verbose) is not bool:
        raise TypeError("progress and verbose must be booleans")
    if not isinstance(exclude, tuple):
        raise TypeError("exclude must be a tuple")
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    if selected is not None and not isinstance(selected, Path):
        raise TypeError("selected must be a Path")
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError(f"root has symlinked path: {root}")
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    selected = (selected or root).resolve()
    if not selected.exists() or (selected != root and root not in selected.parents):
        raise ValueError("selection must be beneath root")
    excluded_root = excluded_root.resolve() if excluded_root else None
    if excluded_root and (
        selected == excluded_root or excluded_root in selected.parents
    ):
        return
    if selected.is_file():
        if (
            not selected.is_symlink()
            and not selected.name.startswith((".afpDeleted", "photos-oxygen-sha"))
            and selected.name not in {".DS_Store", ".photostow.lock", "oxygen-sha"}
            and not any(fnmatch.fnmatch(selected.name, pattern) for pattern in exclude)
        ):
            yield selected
        return
    if selected.name in {"@eaDir", ".objects", "._DAV", "oxygen-sha"}:
        return

    last_report = time.monotonic() - 30
    for directory, dirs, names in os.walk(selected):
        now = time.monotonic()
        if progress and (verbose or now - last_report >= 30):
            print(f"discovering {directory}", flush=True)
            last_report = now
        dirs[:] = [
            name
            for name in dirs
            if name not in {"@eaDir", ".objects", "._DAV"}
            and not (
                excluded_root and (Path(directory) / name).resolve() == excluded_root
            )
            and not any(fnmatch.fnmatch(name, pattern) for pattern in exclude)
        ]
        dirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            if (
                not path.name.startswith((".afpDeleted", "photos-oxygen-sha"))
                and path.name not in {".DS_Store", ".photostow.lock", "oxygen-sha"}
                and not path.is_symlink()
                and not any(fnmatch.fnmatch(name, pattern) for pattern in exclude)
            ):
                yield path


def visible_files(
    root: Path,
    selected: Path | None = None,
    progress: bool = False,
    verbose: bool = False,
    exclude: tuple[str, ...] = (),
    excluded_root: Path | None = None,
) -> list[Path]:
    return list(
        iter_visible_files(root, selected, progress, verbose, exclude, excluded_root)
    )


@contextmanager
def _operation_lock(root: Path):
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    lock_path = Path(tempfile.gettempdir()) / "photostow-ledger.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("another Oxygen operation is running") from error
        yield


def _prune_local_ledger_locked(
    root: Path, ledger: Path, keep: int = 5
) -> tuple[int, int]:
    if type(keep) is not int or keep < 1:
        raise ValueError("keep must be a positive integer")
    if ledger.is_symlink() or any(parent.is_symlink() for parent in ledger.parents):
        raise ValueError(f"ledger path is unsafe: {ledger}")
    ledger.parent.mkdir(parents=True, exist_ok=True)
    old = ledger.read_text(encoding="utf-8").splitlines() if ledger.is_file() else []
    current = {
        canonical_archive_path(path) for path in iter_visible_files(root.resolve())
    }
    pruned = {
        canonical_archive_path(path): digest
        for digest, path in parse_sha_lines(old)
        if canonical_archive_path(path) in current
    }
    payload = "".join(f"{digest}  {path}\n" for path, digest in sorted(pruned.items()))
    for index in range(keep - 1, 0, -1):
        previous = ledger.with_name(f"{ledger.name}.{index}.gz")
        if previous.is_file():
            previous.rename(ledger.with_name(f"{ledger.name}.{index + 1}.gz"))
    if ledger.is_file():
        with ledger.open("rb") as source, gzip.open(
            ledger.with_name(f"{ledger.name}.1.gz"), "wb"
        ) as backup:
            backup.write(source.read())
    fd, temporary_name = tempfile.mkstemp(dir=ledger.parent, prefix=f".{ledger.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, ledger)
    except BaseException:
        os.unlink(temporary_name)
        raise
    return len(old), len(pruned)


def prune_local_ledger(root: Path, ledger: Path, keep: int = 5) -> tuple[int, int]:
    with _operation_lock(root.resolve()):
        return _prune_local_ledger_locked(root, ledger, keep)
