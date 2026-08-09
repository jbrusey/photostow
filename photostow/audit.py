from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from photostow.core import parse_sha_lines
from photostow.remote import remote_sha256, ssh_stdout


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
        f"find {root!r} -path '*/@eaDir' -prune -o -type f -print0 "
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


def duplicate_groups(lines: list[str]) -> list[list[str]]:
    by_hash: dict[str, list[str]] = {}
    for digest, path in parse_sha_lines(lines):
        by_hash.setdefault(digest, []).append(path)
    return [paths for paths in by_hash.values() if len(paths) > 1]


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
