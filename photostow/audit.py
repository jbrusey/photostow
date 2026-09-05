from __future__ import annotations

import posixpath
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from photostow.core import parse_sha_lines
from photostow.paths import canonical_archive_path
from photostow.remote import (
    REMOTE_EXCLUDES,
    SSH,
    remote_path_beneath,
    remote_sha256,
    ssh_stdout,
)


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int


def known_paths(ledger_lines: list[str]) -> set[str]:
    return {path for _, path in parse_sha_lines(ledger_lines)}


def unknown_files(
    remote_files: list[RemoteFile], ledger_lines: list[str]
) -> list[RemoteFile]:
    known = known_paths(ledger_lines)
    return [file for file in remote_files if file.path not in known]


def remote_files(host: str, root: str) -> list[RemoteFile]:
    if not root or not root.strip():
        raise ValueError("remote audit root must not be empty")
    if "\0" in root:
        raise ValueError("remote audit root must not contain NUL")
    root = root.rstrip("/") + "/"
    script = (
        f"find {shlex.quote(root)} {REMOTE_EXCLUDES}"
        "-type f -exec stat -c '%s %n' {} +"
    )
    out = ssh_stdout(host, script)
    files = []
    records = out.split("\0") if "\0" in out else out.splitlines()
    for record in records:
        if not record:
            continue
        size, separator, path = record.partition(" ")
        if not separator or not path:
            raise ValueError("remote audit stat record is malformed")
        if path:
            if not size.isdigit():
                raise ValueError(f"remote audit size is malformed: {size}")
            if not remote_path_beneath(root, path):
                raise ValueError(f"remote audit path escapes root: {path}")
            files.append(RemoteFile(path=posixpath.normpath(path), size=int(size)))
    unique: dict[str, RemoteFile] = {}
    for file in files:
        previous = unique.get(file.path)
        if previous is not None and previous.size != file.size:
            raise ValueError(f"conflicting audit sizes for path: {file.path}")
        unique[file.path] = file
    return list(unique.values())


def human_bytes(n: int) -> str:
    if n < 0:
        raise ValueError("audit byte total must not be negative")
    value = float(n)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if value < 1024 or unit == "PiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def time_range(bytes_total: int) -> str:
    if bytes_total < 0:
        raise ValueError("audit byte total must not be negative")
    low = bytes_total / (80 * 1024 * 1024)
    high = bytes_total / (20 * 1024 * 1024)
    return f"{low / 60:.1f}-{high / 60:.1f} minutes at 20-80 MiB/s"


STANDARD_YEAR_FILE = re.compile(r"^/var/services/photo/\d{4}/[^/]+\.[^/.]+$")


def preferred_path_key(path: str) -> tuple[int, str]:
    if "pixette_removed" in path:
        return (0, path)
    if STANDARD_YEAR_FILE.match(path):
        return (1, path)
    return (2, path)


def sort_duplicate_group(paths: list[str]) -> list[str]:
    return sorted(paths, key=preferred_path_key)


def duplicate_groups(
    lines: list[str], current_paths: set[str] | None = None
) -> list[list[str]]:
    by_hash: dict[str, list[str]] = {}
    for digest, path in parse_sha_lines(lines):
        if current_paths is None or path in current_paths:
            by_hash.setdefault(digest, []).append(path)
    return [sort_duplicate_group(paths) for paths in by_hash.values() if len(paths) > 1]


def pixette_removed_variant(path: str) -> str:
    stem, extension = posixpath.splitext(path)
    suffix = "_pixette_removed"
    return path if stem.endswith(suffix) else stem + suffix + extension


def remote_duplicate_groups(host: str, root: str, ledger: Path) -> list[list[str]]:
    current = {canonical_archive_path(file.path) for file in remote_files(host, root)}
    lines = ledger.read_text(encoding="utf-8").splitlines()
    expanded = []
    seen: set[tuple[str, str]] = set()
    for digest, raw_path in parse_sha_lines(lines):
        path = canonical_archive_path(raw_path)
        candidates = [path]
        variant = pixette_removed_variant(path)
        if variant != path:
            candidates.append(variant)
        for candidate in candidates:
            pair = (digest, candidate)
            if candidate in current and pair not in seen:
                expanded.append(f"{digest}  {candidate}")
                seen.add(pair)
    return duplicate_groups(expanded, current)


def parse_duplicate_group_file(path: Path) -> list[list[str]]:
    if not path.is_file():
        raise ValueError(f"duplicate report input is not a file: {path}")
    if path.is_symlink():
        raise ValueError(f"refusing symlink duplicate report input: {path}")
    for parent in path.parents:
        if parent.is_symlink():
            raise ValueError(
                f"refusing symlink duplicate report input parent: {parent}"
            )
    try:
        text = path.read_bytes().decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as error:
        raise ValueError("duplicate report is not valid UTF-8") from error
    if not text or not text.strip():
        return []
    if "\r" in text:
        raise ValueError("duplicate report path contains unsupported newline")
    groups = [group.split("\n") for group in text.split("\n\n")]
    seen_paths: set[str] = set()
    for group in groups:
        if any(not path.strip() for path in group):
            raise ValueError("duplicate report path must not be empty")
        if any("\0" in path for path in group):
            raise ValueError("duplicate report path must not contain NUL")
        if len(group) < 2:
            raise ValueError("duplicate report group must have two paths")
        if len(set(group)) != len(group):
            raise ValueError("duplicate report group paths must be unique")
        if seen_paths.intersection(group):
            raise ValueError("duplicate report path appears in multiple groups")
        seen_paths.update(group)
    return groups


def verify_duplicate(host: str, keep: str, duplicate: str) -> None:
    cmd = " && ".join(
        [
            f"test -f {shlex.quote(keep)}",
            f"test -f {shlex.quote(duplicate)}",
            f"test $(stat -c %s {shlex.quote(keep)}) = $(stat -c %s {shlex.quote(duplicate)})",
            f"cmp -s {shlex.quote(keep)} {shlex.quote(duplicate)}",
        ]
    )
    subprocess.run([*SSH, host, cmd], check=True)


def delete_duplicate_groups(
    host: str, groups: list[list[str]], dry_run: bool = True
) -> int:
    processed = 0
    failures: list[tuple[str, str]] = []
    for group in groups:
        ordered = sort_duplicate_group(group)
        keep = ordered[0]
        for duplicate in ordered[1:]:
            try:
                verify_duplicate(host, keep, duplicate)
            except subprocess.CalledProcessError:
                failures.append((keep, duplicate))
                continue
            if not dry_run:
                subprocess.run(
                    [*SSH, host, f"rm -- {shlex.quote(duplicate)}"], check=True
                )
            processed += 1
    if failures:
        print(
            f"verification failed for {len(failures)} duplicate files:", file=sys.stderr
        )
        for keep, duplicate in failures:
            print(f"  keep: {keep}\n  skip: {duplicate}", file=sys.stderr)
        raise SystemExit(1)
    return processed


def write_duplicate_groups(groups: list[list[str]], output: Path) -> None:
    if output.is_symlink():
        raise ValueError(f"refusing symlink duplicate report: {output}")
    if output.exists() and not output.is_file():
        raise ValueError(f"duplicate report destination is not a file: {output}")
    for parent in output.parents:
        if parent.is_symlink():
            raise ValueError(f"refusing symlink duplicate report parent: {parent}")
    if not output.parent.is_dir():
        raise ValueError(f"duplicate report parent is not a directory: {output.parent}")
    seen_paths: set[str] = set()
    for group in groups:
        if len(group) < 2:
            raise ValueError("duplicate report group must have two paths")
        if len(set(group)) != len(group):
            raise ValueError("duplicate report group paths must be unique")
        if seen_paths.intersection(group):
            raise ValueError("duplicate report path appears in multiple groups")
        seen_paths.update(group)
        for path in group:
            if not path or not path.strip():
                raise ValueError("duplicate report path must not be empty")
            if "\0" in path:
                raise ValueError("duplicate report path must not contain NUL")
            if "\n" in path or "\r" in path:
                raise ValueError("duplicate report path contains unsupported newline")
    output.write_text(
        "\n\n".join("\n".join(group) for group in groups) + ("\n" if groups else ""),
        encoding="utf-8",
    )


def write_audit(
    host: str, root: str, ledger: Path, workdir: Path, do_hash: bool = False
) -> str:
    workdir.mkdir(parents=True, exist_ok=True)
    ledger_lines = ledger.read_text(encoding="utf-8").splitlines()
    files = remote_files(host, root)
    unknown = unknown_files(files, ledger_lines)
    total = sum(file.size for file in unknown)

    (workdir / "unknown-paths.txt").write_text(
        "".join(f"{file.path}\n" for file in unknown), encoding="utf-8"
    )
    summary = [
        f"remote files: {len(files)}",
        f"unknown paths needing SHA: {len(unknown)}",
        f"unknown bytes: {human_bytes(total)}",
        f"hash estimate: {time_range(total)}",
        f"workdir: {workdir}",
    ]
    if not do_hash:
        summary.append("no hashes computed; rerun with --hash to continue")
        return "\n".join(summary)

    new_hashes = remote_sha256(host, [file.path for file in unknown])
    (workdir / "new-hashes.sha256").write_text(new_hashes, encoding="utf-8")
    combined = ledger_lines + new_hashes.splitlines()
    groups = duplicate_groups(combined)
    (workdir / "duplicate-groups.txt").write_text(
        "\n".join("\n".join(group) + "\n" for group in groups), encoding="utf-8"
    )
    summary.extend(
        [
            f"new hashes written: {workdir / 'new-hashes.sha256'}",
            f"duplicate groups: {len(groups)}",
            f"duplicate report: {workdir / 'duplicate-groups.txt'}",
        ]
    )
    return "\n".join(summary)
