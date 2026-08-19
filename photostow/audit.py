from __future__ import annotations

import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from photostow.core import parse_sha_lines
from photostow.remote import SSH, remote_sha256, ssh_stdout


@dataclass(frozen=True)
class RemoteFile:
    path: str
    size: int


def known_paths(ledger_lines: list[str]) -> set[str]:
    return {path for _, path in parse_sha_lines(ledger_lines)}


def unknown_files(remote_files: list[RemoteFile], ledger_lines: list[str]) -> list[RemoteFile]:
    known = known_paths(ledger_lines)
    return [file for file in remote_files if file.path not in known]


def remote_files(host: str, root: str) -> list[RemoteFile]:
    root = root.rstrip("/") + "/"
    script = (
        f"find {root!r} -path '*/@eaDir' -prune -o "
        "-name 'photos-oxygen-sha*' -prune -o -type f -print0 "
        "| xargs -0 stat -c '%s %n'"
    )
    out = ssh_stdout(host, script)
    files = []
    for line in out.splitlines():
        size, _, path = line.partition(" ")
        if path:
            files.append(RemoteFile(path=path, size=int(size)))
    return files


def human_bytes(n: int) -> str:
    value = float(n)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def time_range(bytes_total: int) -> str:
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


def duplicate_groups(lines: list[str], current_paths: set[str] | None = None) -> list[list[str]]:
    by_hash: dict[str, list[str]] = {}
    for digest, path in parse_sha_lines(lines):
        if current_paths is None or path in current_paths:
            by_hash.setdefault(digest, []).append(path)
    return [sort_duplicate_group(paths) for paths in by_hash.values() if len(paths) > 1]


def remote_duplicate_groups(host: str, root: str, ledger: Path) -> list[list[str]]:
    current = {file.path for file in remote_files(host, root)}
    lines = ledger.read_text(encoding="utf-8").splitlines()
    return duplicate_groups(lines, current)


def parse_duplicate_group_file(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [group.splitlines() for group in text.split("\n\n")]


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


def delete_duplicate_groups(host: str, groups: list[list[str]], dry_run: bool = True) -> int:
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
                subprocess.run([*SSH, host, f"rm -- {shlex.quote(duplicate)}"], check=True)
            processed += 1
    if failures:
        print(f"verification failed for {len(failures)} duplicate files:", file=sys.stderr)
        for keep, duplicate in failures:
            print(f"  keep: {keep}\n  skip: {duplicate}", file=sys.stderr)
        raise SystemExit(1)
    return processed


def write_duplicate_groups(groups: list[list[str]], output: Path) -> None:
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
