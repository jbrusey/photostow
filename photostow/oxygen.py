from __future__ import annotations

import fcntl
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from photostow.core import sha256_file

DEFAULT_ARCHIVE_ROOT = Path("/var/services/photo")
DEFAULT_OBJECT_ROOT = Path("/volume1/photostow")


def default_object_root(root: Path) -> Path:
    """Use the production store by default; keep arbitrary local roots self-contained."""
    return (
        DEFAULT_OBJECT_ROOT
        if root.resolve() == DEFAULT_ARCHIVE_ROOT.resolve()
        else root / ".objects"
    )


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
    if any(type(pattern) is not str for pattern in exclude):
        raise ValueError("exclude patterns must be strings")
    if not isinstance(root, Path):
        raise TypeError("root must be a Path")
    if selected is not None and not isinstance(selected, Path):
        raise TypeError("selected must be a Path")
    root_input = root
    if root_input.is_symlink():
        raise ValueError(f"root is a symlink: {root_input}")
    if any(parent.is_symlink() for parent in root_input.parents):
        raise ValueError(f"root has symlinked parent: {root_input}")
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    selected_input = selected
    if selected_input is not None:
        if selected_input.is_symlink():
            raise ValueError(f"selection is a symlink: {selected_input}")
        if any(parent.is_symlink() for parent in selected_input.parents):
            raise ValueError(f"selection has symlinked parent: {selected_input}")
    selected = (selected or root).resolve()
    if not selected.exists():
        raise FileNotFoundError(selected)
    if selected != root and root not in selected.parents:
        raise ValueError("selection must be beneath root")
    if excluded_root is not None and not isinstance(excluded_root, Path):
        raise ValueError("excluded root must be a Path")
    if excluded_root is not None:
        if excluded_root.is_symlink():
            raise ValueError(f"excluded root is a symlink: {excluded_root}")
        if excluded_root.exists() and not excluded_root.is_dir():
            raise NotADirectoryError(excluded_root)
        for parent in excluded_root.parents:
            if parent.exists() and not parent.is_dir():
                raise NotADirectoryError(parent)
        if any(parent.is_symlink() for parent in excluded_root.parents):
            raise ValueError(f"excluded root has symlinked parent: {excluded_root}")
        excluded_root = excluded_root.resolve()
    if excluded_root and (
        selected == excluded_root or excluded_root in selected.parents
    ):
        return
    if selected.is_file():
        if (
            selected.is_symlink()
            or selected.name.startswith(".afpDeleted")
            or selected.name in {".DS_Store", ".photostow.lock"}
            or any(fnmatch.fnmatch(selected.name, pattern) for pattern in exclude)
        ):
            return
        if selected.name.startswith("photos-oxygen-sha"):
            return
        yield selected
        return
    if selected.name in {"@eaDir", ".objects", "._DAV"} or any(
        fnmatch.fnmatch(selected.name, pattern) for pattern in exclude
    ):
        return

    def onerror(error: OSError) -> None:
        raise error

    last_report = time.monotonic() - 30
    for directory, dirs, names in os.walk(selected, onerror=onerror):
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
                not path.name.startswith("photos-oxygen-sha")
                and not path.name.startswith(".afpDeleted")
                and path.name not in {".DS_Store", ".photostow.lock"}
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


def object_path(root: Path, digest: str, object_root: Path | None = None) -> Path:
    return (
        (object_root or default_object_root(root)) / "sha256" / digest[:2] / digest[2:]
    )


def _device(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return os.stat(probe).st_dev


def _check_same_filesystem(root: Path, object_root: Path) -> None:
    if object_root.is_symlink():
        raise ValueError(f"object root must not be a symlink: {object_root}")
    for parent in object_root.parents:
        if parent.is_symlink():
            raise ValueError(f"object root parent must not be a symlink: {parent}")
    if object_root.exists() and not object_root.is_dir():
        raise NotADirectoryError(object_root)
    store = object_root / "sha256"
    if store.is_symlink():
        raise ValueError(f"object store must not be a symlink: {store}")
    if _device(root) != _device(object_root):
        raise ValueError("object root must be on the same filesystem as root")


def _open_nofollow(path: Path):
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe no-follow file opening is unavailable")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise OSError(f"cannot open file safely: {path}") from error
    return os.fdopen(fd, "rb")


def _stable_digest(path: Path) -> tuple[str, os.stat_result]:
    with _open_nofollow(path) as source:
        before = os.fstat(source.fileno())
        hasher = hashlib.sha256()
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            hasher.update(chunk)
        after = os.fstat(source.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    pathname = path.stat()
    if any(getattr(before, field) != getattr(after, field) for field in fields):
        raise OSError(f"file changed while hashing: {path}")
    if (pathname.st_dev, pathname.st_ino) != (before.st_dev, before.st_ino):
        raise OSError(f"file replaced while hashing: {path}")
    return hasher.hexdigest(), after


def _check_hardlink_support(root: Path) -> None:
    source = target = None
    source_fd = target_fd = None
    try:
        source_fd, source_name = tempfile.mkstemp(dir=root)
        source = Path(source_name)
        target_fd, target_name = tempfile.mkstemp(dir=root)
        target = Path(target_name)
        os.close(source_fd)
        source_fd = None
        os.close(target_fd)
        target_fd = None
        target.unlink()
        try:
            os.link(source, target, follow_symlinks=False)
        except OSError as error:
            raise OSError(f"hardlinks are unavailable on filesystem: {root}") from error
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if target_fd is not None:
            os.close(target_fd)
        if source is not None:
            source.unlink(missing_ok=True)
        if target is not None:
            target.unlink(missing_ok=True)


def _check_resources(path: Path, needed_inodes: int) -> None:
    usage = shutil.disk_usage(path)
    if usage.free <= 0:
        raise OSError(f"no free bytes on filesystem: {path}")
    statvfs = os.statvfs(path)
    if statvfs.f_favail < needed_inodes:
        raise OSError(f"not enough free inodes on filesystem: {path}")


def _verify_object(path: Path, digest: str) -> None:
    if path.is_symlink():
        raise OSError(f"object path is a symlink: {path}")
    if sha256_file(path) != digest:
        raise OSError(f"object content does not match digest: {path}")


def gc_candidates(object_root: Path, root: Path | None = None) -> list[Path]:
    if root is not None:
        _check_same_filesystem(root.resolve(), object_root)
        with _operation_lock(root):
            return _gc_candidates(object_root)
    return _gc_candidates(object_root)


def gc_summary(object_root: Path, root: Path | None = None) -> tuple[list[Path], int]:
    if root is not None:
        _check_same_filesystem(root.resolve(), object_root)
        with _operation_lock(root):
            return _gc_summary(object_root)
    return _gc_summary(object_root)


def _gc_summary(object_root: Path) -> tuple[list[Path], int]:
    candidates = _gc_candidates(object_root)
    store = object_root.resolve() / "sha256"
    retained = sum(
        1
        for path in store.rglob("*")
        if path.is_file() and not path.is_symlink() and path.stat().st_nlink != 1
    )
    return candidates, retained


def _gc_candidates(object_root: Path) -> list[Path]:
    if object_root.is_symlink():
        raise ValueError(f"object root must not be a symlink: {object_root}")
    for parent in object_root.parents:
        if parent.is_symlink():
            raise ValueError(f"object root parent must not be a symlink: {parent}")
    verification_errors = verify_objects(object_root)
    if verification_errors:
        raise OSError(
            "object store verification failed: " + "; ".join(verification_errors)
        )
    store = object_root.resolve() / "sha256"
    if not store.is_dir():
        return []
    candidates: list[Path] = []
    for path in sorted(item for item in store.rglob("*") if item.is_file()):
        if path.is_symlink():
            continue
        relative = path.relative_to(store)
        digest = (
            relative.parts[0] + relative.parts[1] if len(relative.parts) == 2 else ""
        )
        if re.fullmatch(r"[0-9a-f]{64}", digest) and path.stat().st_nlink == 1:
            _verify_object(path, digest)
            candidates.append(path)
    return candidates


def verify_objects(
    object_root: Path,
    selected: Path | None = None,
    limit: int | None = None,
    progress: bool = False,
) -> list[str]:
    if limit is not None and type(limit) is not int:
        raise ValueError("limit must be a nonnegative integer")
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    errors: list[str] = []
    if object_root.is_symlink():
        return [f"object root is a symlink: {object_root}"]
    for parent in object_root.parents:
        if parent.is_symlink():
            return [f"object root parent is a symlink: {parent}"]
    store = object_root.resolve() / "sha256"
    if store.is_symlink():
        return [f"object store is a symlink: {store}"]
    if not store.is_dir():
        return [f"missing object store: {store}"]
    entries = sorted(store.rglob("*"))
    if selected:
        if not str(selected).strip():
            raise ValueError("verification path must not be empty")
        if "\0" in str(selected):
            raise ValueError("verification path must not contain NUL")
        if selected.is_absolute():
            raise ValueError("verification path must be relative")
        selected_path = store / selected
        resolved_selected = selected_path.resolve()
        if store not in resolved_selected.parents:
            raise ValueError("verification path must be beneath object store")
        if selected_path.is_symlink():
            raise ValueError(f"verification path is a symlink: {selected}")
        if any(parent.is_symlink() for parent in selected_path.parents):
            raise ValueError(f"verification path has symlinked parent: {selected}")
        selected = resolved_selected
        if store not in selected.parents:
            raise ValueError("verification path must be beneath object store")
        entries = [
            path for path in entries if path == selected or selected in path.parents
        ]
    namespace_entries = entries[:limit] if limit is not None else entries
    paths = [path for path in entries if path.is_file() and not path.is_symlink()]
    paths = paths[:limit]
    for entry in namespace_entries:
        if entry.is_symlink():
            errors.append(f"symlink object path: {entry}")
            continue
        relative = entry.relative_to(store)
        valid_shard = len(relative.parts) == 1 and re.fullmatch(
            r"[0-9a-f]{2}", relative.name
        )
        if entry.is_dir() and not valid_shard:
            errors.append(f"malformed object path: {entry}")
    for index, path in enumerate(paths, 1):
        if path.is_symlink():
            errors.append(f"symlink object path: {path}")
            continue
        if progress:
            print(f"verifying {index}/{len(paths)} {path}", flush=True)
        relative = path.relative_to(store)
        digest = (
            relative.parts[0] + relative.parts[1] if len(relative.parts) == 2 else ""
        )
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            errors.append(f"malformed object path: {path}")
            continue
        if sha256_file(path) != digest:
            errors.append(f"content mismatch: {path}")
    return errors


def ingest(
    source: Path,
    destination: Path,
    root: Path,
    object_root: Path | None = None,
    safe_verify: bool = False,
) -> str:
    with _operation_lock(root):
        return _ingest_locked(source, destination, root, object_root, safe_verify)


def _ingest_locked(
    source: Path,
    destination: Path,
    root: Path,
    object_root: Path | None = None,
    safe_verify: bool = False,
) -> str:
    if source.is_symlink() or any(parent.is_symlink() for parent in source.parents):
        raise ValueError("source must be a regular file")
    source = source.resolve()
    root = root.resolve()
    if destination.is_symlink() or any(
        parent.is_symlink() for parent in destination.parents
    ):
        raise FileExistsError(destination)
    destination = destination.resolve()
    if not source.is_file() or source.is_symlink():
        raise ValueError("source must be a regular file")
    if root not in destination.parents:
        raise ValueError("destination must be beneath root")
    object_root = object_root or default_object_root(root)
    _check_same_filesystem(root, object_root)
    object_root = object_root.resolve()
    digest, source_stat = _stable_digest(source)
    if destination.exists():
        if destination.is_file() and _stable_digest(destination)[0] == digest:
            return digest
        raise FileExistsError(destination)
    _check_resources(root, 2)
    _check_hardlink_support(root)
    target = object_path(root, digest, object_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.is_symlink():
            raise OSError(f"object path is a symlink: {target}")
        if target.stat().st_size != source_stat.st_size:
            raise OSError(f"object size does not match source: {target}")
        if safe_verify:
            _verify_object(target, digest)
        if target.stat().st_nlink != 1:
            raise ValueError("object already has a visible reference")
    else:
        fd, name = tempfile.mkstemp(dir=target.parent)
        os.close(fd)
        temporary = Path(name)
        try:
            with _open_nofollow(source) as source_file, temporary.open(
                "wb"
            ) as target_file:
                shutil.copyfileobj(source_file, target_file, 1024 * 1024)
                target_file.flush()
                os.fsync(target_file.fileno())
            _verify_object(temporary, digest)
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                _verify_object(target, digest)
        finally:
            temporary.unlink(missing_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(target, destination, follow_symlinks=False)
    except FileExistsError:
        raise FileExistsError(destination) from None
    return digest


def _ensure_object(source: Path, target: Path, digest: str) -> None:
    if target.exists():
        _verify_object(target, digest)
        return
    try:
        os.link(source, target, follow_symlinks=False)
    except FileExistsError:
        _verify_object(target, digest)
        return
    try:
        _verify_object(target, digest)
    except OSError:
        target.unlink(missing_ok=True)
        raise


def _record_failure(
    failure_list: Path,
    root: Path,
    path: Path,
    digest: str | None,
    reason: str,
    object_root: Path,
) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": str(path),
        "digest": digest,
        "object_path": str(object_path(root, digest, object_root)) if digest else None,
        "reason": reason,
    }
    if failure_list.is_symlink() or any(
        parent.is_symlink() for parent in failure_list.parents
    ):
        raise ValueError(f"failure list path is unsafe: {failure_list}")
    failure_list.parent.mkdir(parents=True, exist_ok=True)
    with failure_list.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False) + "\n")
        output.flush()
        os.fsync(output.fileno())


def _process_record(
    root: Path,
    object_root: Path,
    digest: str,
    path: Path,
    dry_run: bool,
    verbose: bool,
) -> None:
    if path.is_symlink():
        raise OSError(f"migration path is a symlink: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise OSError(f"migration path parent is a symlink: {parent}")
    obj = object_path(root, digest, object_root)
    for parent in obj.parents:
        if parent.is_symlink():
            raise OSError(f"object path parent is a symlink: {parent}")
    if obj.exists():
        _verify_object(obj, digest)
        if os.path.samefile(path, obj):
            if path.stat().st_nlink != 2:
                raise OSError(
                    f"already-migrated inode has unexpected link count: {path}"
                )
            return
        raise OSError(f"object already exists for different inode: {obj}")
    if path.stat().st_nlink != 1:
        raise OSError(f"source has unexpected link count: {path}")
    if dry_run:
        if verbose:
            action = "link" if obj.exists() else "create"
            print(f"{action} {path} -> {obj}")
        return
    obj.parent.mkdir(parents=True, exist_ok=True)
    _ensure_object(path, obj, digest)
    current_digest, _ = _stable_digest(path)
    if current_digest != digest:
        raise OSError(f"file changed while publishing: {path}")
    if not os.path.samefile(path, obj):
        fd, name = tempfile.mkstemp(dir=path.parent)
        os.close(fd)
        replacement = Path(name)
        replacement.unlink()
        try:
            os.link(obj, replacement, follow_symlinks=False)
            # Python's portable stdlib has no compare-and-swap path replacement.
            os.replace(replacement, path)
        finally:
            replacement.unlink(missing_ok=True)


def _lock_path(root: Path) -> Path:
    key = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    return Path(tempfile.gettempdir()) / f"photostow-{key}.lock"


@contextmanager
def _operation_lock(root: Path):
    root = root.resolve()
    if not root.is_dir():
        raise NotADirectoryError(root)
    if not hasattr(os, "O_NOFOLLOW"):
        raise OSError("safe no-follow lock opening is unavailable")
    lock_path = _lock_path(root)
    fd = os.open(
        lock_path,
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
        0o600,
    )
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "r+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OSError("another Oxygen operation is running") from error
        yield


def migrate(
    root: Path,
    dry_run: bool = True,
    selected: Path | None = None,
    limit: int | None = None,
    object_root: Path | None = None,
    manifest: Path | None = None,
    apply_manifest: Path | None = None,
    exclude: tuple[str, ...] = (),
    verbose: bool = False,
    failure_list: Path | None = None,
    continue_on_error: bool = False,
) -> int:
    if limit is not None and type(limit) is not int:
        raise ValueError("limit must be a nonnegative integer")
    if limit is not None and limit < 0:
        raise ValueError("limit must not be negative")
    root = root.resolve()
    with _operation_lock(root):
        return _migrate_locked(
            root,
            dry_run,
            selected,
            limit,
            object_root,
            manifest,
            apply_manifest,
            exclude,
            verbose,
            failure_list,
            continue_on_error,
        )


def _migrate_locked(
    root: Path,
    dry_run: bool = True,
    selected: Path | None = None,
    limit: int | None = None,
    object_root: Path | None = None,
    manifest: Path | None = None,
    apply_manifest: Path | None = None,
    exclude: tuple[str, ...] = (),
    verbose: bool = False,
    failure_list: Path | None = None,
    continue_on_error: bool = False,
) -> int:
    root = root.resolve()
    errors: list[str] = []
    records: (
        list[tuple[str, Path, os.stat_result]]
        | Iterator[tuple[str, Path, os.stat_result]]
    ) = []
    streaming = False
    if apply_manifest:
        if apply_manifest.is_symlink():
            raise ValueError(f"refusing symlink apply manifest: {apply_manifest}")
        if any(parent.is_symlink() for parent in apply_manifest.parents):
            raise ValueError(
                f"refusing symlink apply manifest parent: {apply_manifest}"
            )
        try:
            text = apply_manifest.read_bytes().decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("apply manifest is not valid UTF-8") from error
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("apply manifest is not valid JSON") from error
        required = {"version", "root", "object_root", "records"}
        record_fields = {"path", "digest", "size", "device", "inode", "mtime_ns"}
        if (
            not isinstance(data, dict)
            or type(data.get("version")) is not int
            or data.get("version") != 1
            or not required.issubset(data)
            or not isinstance(data.get("root"), str)
            or not data["root"].strip()
            or not isinstance(data.get("object_root"), str)
            or not data["object_root"].strip()
            or not Path(data["root"]).is_absolute()
            or not Path(data["object_root"]).is_absolute()
            or not isinstance(data.get("records"), list)
            or any(
                not isinstance(item, dict)
                or not record_fields.issubset(item)
                or not isinstance(item.get("path"), str)
                or not item["path"].strip()
                or Path(item["path"]).is_absolute()
                or not isinstance(item.get("digest"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item.get("digest", ""))
                or any(
                    type(item.get(field)) is not int or item[field] < 0
                    for field in ("size", "device", "inode", "mtime_ns")
                )
                for item in data["records"]
            )
        ):
            raise ValueError("unsupported manifest schema")
        manifest_paths = [item["path"] for item in data["records"]]
        if len(set(manifest_paths)) != len(manifest_paths):
            raise ValueError("manifest contains duplicate paths")
        if Path(data["root"]).resolve() != root:
            raise ValueError("manifest root does not match root")
        object_root = object_root or Path(data["object_root"])
        _check_same_filesystem(root, object_root)
        object_root = object_root.resolve()
        records = []
        for item in data["records"]:
            path = root / str(item.get("path", "<unknown>"))
            try:
                resolved_path = path.resolve()
                if root not in resolved_path.parents:
                    raise ValueError("manifest path is outside root")
                if path.is_symlink():
                    raise ValueError(f"manifest path is a symlink: {path}")
                if any(parent.is_symlink() for parent in path.parents):
                    raise ValueError(f"manifest path has symlinked parent: {path}")
                path = resolved_path
                if root not in path.parents:
                    raise ValueError("manifest path is outside root")
                stat = path.stat()
                fields = (
                    ("st_size", "size"),
                    ("st_dev", "device"),
                    ("st_ino", "inode"),
                    ("st_mtime_ns", "mtime_ns"),
                )
                metadata_changed = any(
                    getattr(stat, key) != item[value] for key, value in fields
                )
                digest, current_stat = _stable_digest(path)
                if digest != item["digest"]:
                    raise ValueError(f"file changed since manifest: {path}")
                if metadata_changed:
                    stat = current_stat
                records.append((digest, path, stat))
            except (OSError, ValueError, TypeError, KeyError) as error:
                errors.append(f"{path}: {error}")
    else:
        object_root = object_root or default_object_root(root)
        _check_same_filesystem(root, object_root)
        assert object_root is not None
        object_root = object_root.resolve()
        discovery_exclude = exclude + ((manifest.name,) if manifest else ())
        discovered = iter_visible_files(
            root,
            selected,
            progress=True,
            verbose=verbose,
            exclude=discovery_exclude,
            excluded_root=object_root,
        )
        if dry_run:
            paths = list(discovered)[:limit]
            records = []
            last_report = time.monotonic() - 30
            for index, path in enumerate(paths, 1):
                now = time.monotonic()
                if verbose or now - last_report >= 30:
                    print(f"hashing {index}/{len(paths)} {path}", flush=True)
                    last_report = now
                try:
                    digest, stat = _stable_digest(path)
                except OSError as error:
                    errors.append(f"{path}: {error}")
                    continue
                records.append((digest, path, stat))
        else:
            streaming = True

            def hashed_records() -> Iterator[tuple[str, Path, os.stat_result]]:
                for index, path in enumerate(discovered, 1):
                    if limit is not None and index > limit:
                        break
                    if verbose:
                        print(f"hashing {index} {path}", flush=True)
                    try:
                        digest, stat = _stable_digest(path)
                    except OSError as error:
                        reason = str(error)
                        errors.append(f"{path}: {reason}")
                        assert object_root is not None
                        _record_failure(
                            failure_list or Path.cwd() / "migration-failures.jsonl",
                            root,
                            path,
                            None,
                            reason,
                            object_root,
                        )
                        if not continue_on_error:
                            raise OSError(f"migration failed: {path}: {reason}")
                        continue
                    yield digest, path, stat

            records = hashed_records()
    object_root = object_root.resolve()
    _check_same_filesystem(root, object_root)
    if not dry_run:
        if not streaming:
            assert isinstance(records, list)
            _check_resources(root, len(records))
        _check_hardlink_support(root)
    if manifest and dry_run:
        payload = (
            json.dumps(
                {
                    "version": 1,
                    "root": str(root),
                    "object_root": str(object_root),
                    "records": [
                        {
                            "path": str(path.relative_to(root)),
                            "digest": digest,
                            "size": stat.st_size,
                            "device": stat.st_dev,
                            "inode": stat.st_ino,
                            "mtime_ns": stat.st_mtime_ns,
                        }
                        for digest, path, stat in records
                    ],
                },
                indent=2,
            )
            + "\n"
        )
        fd, name = tempfile.mkstemp(dir=manifest.parent, prefix=f".{manifest.name}.")
        temporary = Path(name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, manifest)
            directory_fd = os.open(manifest.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
    failure_list = failure_list or Path.cwd() / "migration-failures.jsonl"
    processed = 0
    for digest, path, _ in records:
        try:
            if streaming:
                _check_resources(root, 1)
            _process_record(root, object_root, digest, path, dry_run, verbose)
            processed += 1
            if streaming and verbose:
                print(f"committed {path}", flush=True)
        except (OSError, ValueError) as error:
            reason = str(error)
            errors.append(f"{path}: {reason}")
            _record_failure(failure_list, root, path, digest, reason, object_root)
            if not continue_on_error:
                break
    if errors:
        raise OSError("migration failed: " + "; ".join(errors))
    if streaming:
        return processed
    assert isinstance(records, list)
    return len(records)
