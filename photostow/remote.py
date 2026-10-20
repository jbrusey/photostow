from __future__ import annotations

import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from photostow.core import paths_not_in_ledger, prune_ledger_lines

SSH = ["ssh", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=120"]
TAR = [shutil.which("gtar") or "tar", "--no-xattrs"]
TAR_PROGRESS = (
    ["--checkpoint=10000", "--checkpoint-action=dot"] if TAR[0].endswith("gtar") else []
)
REMOTE_EXCLUDES = (
    "-path '*/@eaDir' -prune -o "
    "-path '*/.objects' -prune -o "
    "-path '*/._DAV' -prune -o "
    "-name '.afpDeleted*' -prune -o "
    "-name 'photos-oxygen-sha*' -prune -o "
)


def remote_path_beneath(root: str, path: str) -> bool:
    """Return whether path is a strict, normalized descendant of root."""
    if not root or not root.strip() or "\0" in root or "\0" in path:
        return False
    normalized_root = posixpath.normpath(root)
    normalized_path = posixpath.normpath(path)
    if normalized_root.startswith("//"):
        normalized_root = "/" + normalized_root.lstrip("/")
    if normalized_path.startswith("//"):
        normalized_path = "/" + normalized_path.lstrip("/")
    if normalized_root == ".":
        return normalized_path not in (".", "..") and not normalized_path.startswith(
            ("../", "/")
        )
    return normalized_path.startswith(normalized_root.rstrip("/") + "/")


@dataclass(frozen=True)
class MissingRecord:
    digest: str
    created: str
    adjusted: bool
    path: Path

    @property
    def year(self) -> str:
        return self.created[:4] if len(self.created) >= 4 else "unknown"


def ssh_stdout(host: str, command: str) -> str:
    result = subprocess.run([*SSH, host, command], check=True, stdout=subprocess.PIPE)
    try:
        return result.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ValueError("remote SSH output is not UTF-8") from exc


def remote_find(host: str, root: str) -> list[str]:
    if not root or not root.strip():
        raise ValueError("remote discovery root must not be empty")
    if "\0" in root:
        raise ValueError("remote discovery root must not contain NUL")
    find_root = root.rstrip("/") + "/"
    cmd = f"find {shlex.quote(find_root)} {REMOTE_EXCLUDES}-type f -print0"
    result = subprocess.run(
        [*SSH, host, cmd], check=True, stdout=subprocess.PIPE, stderr=None
    )
    try:
        output = result.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ValueError("remote discovery output is not UTF-8") from exc
    paths = [path for path in output.split("\0") if path]
    normalized_paths = []
    for path in paths:
        if not remote_path_beneath(find_root, path):
            raise ValueError(f"remote discovery path escapes root: {path}")
        normalized_paths.append(posixpath.normpath(path))
    return list(dict.fromkeys(normalized_paths))


def remote_sha256(host: str, paths: list[str]) -> str:
    if not paths:
        return ""
    data = "\0".join(paths).encode() + b"\0"
    result = subprocess.run(
        [*SSH, host, "xargs -0 sha256sum --zero"],
        input=data,
        check=True,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    try:
        output = result.stdout.decode()
    except UnicodeDecodeError as exc:
        raise ValueError("remote hash output is not UTF-8") from exc
    records = [record for record in output.split("\0") if record]
    for record in records:
        if "\n" in record:
            raise ValueError("remote hash output contains unsupported newline path")
        if not re.fullmatch(r"[0-9a-fA-F]{64}  .+", record):
            raise ValueError("remote hash output is malformed")
    return "\n".join(records) + ("\n" if records else "")


def missing_records(tsv: Path) -> list[MissingRecord]:
    with tsv.open(encoding="utf-8") as f:
        header = next(f, "").rstrip("\n").split("\t")
        if header != ["sha256", "created", "adjusted", "path"]:
            raise ValueError(
                "missing TSV must have columns: sha256, created, adjusted, path"
            )
        rows = []
        for line in f:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 4:
                raise ValueError("missing TSV row must have four columns")
            digest, created, adjusted, path = fields
            if adjusted not in {"0", "1"}:
                raise ValueError("missing TSV adjusted flag must be 0 or 1")
            rows.append(MissingRecord(digest, created, adjusted == "1", Path(path)))
        return rows


def paths_from_missing_tsv(tsv: Path) -> list[Path]:
    return [record.path for record in missing_records(tsv)]


def relative_paths(paths: list[Path], root: Path) -> list[str]:
    relative = []
    for path in paths:
        try:
            relative.append(str(path.relative_to(root)))
        except ValueError as error:
            raise ValueError(f"path is outside source root: {path}") from error
    return relative


def validate_source_paths(paths: list[str], source_root: Path) -> None:
    resolved_root = source_root.resolve()
    for path in paths:
        if Path(path).is_absolute():
            raise ValueError(f"source path must be relative: {path}")
        candidate = source_root / path
        try:
            candidate.resolve().relative_to(resolved_root)
        except ValueError:
            raise ValueError(f"source path escapes source root: {path}")
        if candidate.is_symlink():
            raise ValueError(f"refusing symlink source path: {path}")


def copy_paths_tar(
    paths: list[str], source_root: Path, host: str, dest_root: str
) -> int:
    if not paths:
        return 0
    validate_source_paths(paths, source_root)
    with tempfile.NamedTemporaryFile("wb") as f:
        f.write("\0".join(paths).encode() + b"\0")
        f.flush()
        mkdir = f"mkdir -p {shlex.quote(dest_root)}"
        extract = f"cd {shlex.quote(dest_root)} && tar -xf -"
        subprocess.run([*SSH, host, mkdir], check=True)
        with subprocess.Popen(
            [
                *TAR,
                *TAR_PROGRESS,
                "-cf",
                "-",
                "-C",
                str(source_root),
                "--null",
                "-T",
                f.name,
            ],
            env={**os.environ, "COPYFILE_DISABLE": "1"},
            stdout=subprocess.PIPE,
        ) as tar:
            subprocess.run([*SSH, host, extract], stdin=tar.stdout, check=True)
            if tar.stdout:
                tar.stdout.close()
            if tar.wait() != 0:
                raise subprocess.CalledProcessError(tar.returncode, tar.args)
    return len(paths)


def stage_by_year(records: list[MissingRecord], source_root: Path, stage: Path) -> int:
    count = 0
    resolved_root = source_root.resolve()
    if stage.is_symlink():
        raise ValueError(f"refusing symlink review stage: {stage}")
    for record in records:
        rel = record.path.relative_to(source_root)
        src = source_root / rel
        if src.is_symlink() or not src.is_file():
            continue
        try:
            src.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        name = src.name
        year_dir = stage / record.year
        if year_dir.is_symlink():
            raise ValueError(f"refusing symlink review year: {year_dir}")
        dest = year_dir / name
        if dest.exists():
            dest = stage / record.year / f"{record.digest[:12]}-{name}"
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.link(src, dest)
        count += 1
    return count


def chmod_years(host: str, dest_root: str, years: list[str]) -> None:
    for year in years:
        path = f"{dest_root.rstrip('/')}/{year}"
        cmd = (
            "set -e; "
            f"find {shlex.quote(path)} -path '*/@eaDir' -prune -o "
            f"-type d -exec chmod u+rwx,go+rwx {{}} + -o "
            f"-type f -exec chmod u+rw,go+r {{}} +"
        )
        subprocess.run([*SSH, host, cmd], check=True)


def copy_stage(stage: Path, host: str, dest_root: str) -> None:
    years = sorted(
        path.name for path in stage.iterdir() if path.is_dir() and not path.is_symlink()
    )
    files = [
        str(path.relative_to(stage))
        for path in stage.rglob("*")
        if path.is_file() and not path.is_symlink()
    ]
    if not files:
        return
    subprocess.run([*SSH, host, f"mkdir -p {shlex.quote(dest_root)}"], check=True)
    with tempfile.NamedTemporaryFile("wb") as f:
        f.write("\0".join(files).encode() + b"\0")
        f.flush()
        print(f"copying {len(files)} files", file=sys.stderr)
        tar_cmd = [
            *TAR,
            *TAR_PROGRESS,
            "-cf",
            "-",
            "-C",
            str(stage),
            "--null",
            "-T",
            f.name,
        ]
        tar = subprocess.Popen(
            tar_cmd,
            env={**os.environ, "COPYFILE_DISABLE": "1"},
            stdout=subprocess.PIPE,
        )
        subprocess.run(
            [*SSH, host, f"cd {shlex.quote(dest_root)} && tar -xf -"],
            stdin=tar.stdout,
            check=True,
        )
        if tar.stdout:
            tar.stdout.close()
        if tar.wait() != 0:
            raise subprocess.CalledProcessError(tar.returncode, tar.args)
    chmod_years(host, dest_root, years)


def copy_records_by_year(
    records: list[MissingRecord], source_root: Path, host: str, dest_root: str
) -> int:
    by_year: dict[str, list[MissingRecord]] = defaultdict(list)
    for record in records:
        by_year[record.year].append(record)

    total = 0
    for year, year_records in sorted(by_year.items()):
        with tempfile.TemporaryDirectory() as tmp:
            stage = Path(tmp) / "stage"
            count = stage_by_year(year_records, source_root, stage)
            if count:
                copy_stage(stage, host, dest_root)
                total += count
    return total


def prune_remote_ledger(
    host: str, root: str, ledger: Path, output: Path | None = None
) -> tuple[int, int]:
    old = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    current = set(remote_find(host, root))
    pruned = prune_ledger_lines(old, current)
    target = output or ledger
    target.write_text("\n".join(pruned) + ("\n" if pruned else ""), encoding="utf-8")
    return len(old), len(pruned)


def update_remote_ledger(
    host: str, root: str, ledger: Path, output: Path | None = None
) -> int:
    old = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    new_paths = paths_not_in_ledger(remote_find(host, root), old)
    new_hashes = remote_sha256(host, new_paths) if new_paths else ""
    target = output or ledger
    text = "\n".join(old)
    if text and new_hashes:
        text += "\n"
    text += new_hashes.rstrip("\n")
    if text:
        text += "\n"
    target.write_text(text, encoding="utf-8")
    return len(new_paths)


def install_remote_ledger(
    host: str, local_ledger: Path, remote_ledger: str, keep: int = 5
) -> None:
    quoted = shlex.quote(remote_ledger)
    rotate = [
        "set -e",
        (
            f"for i in $(seq {keep - 1} -1 1); do "
            f"test -f {quoted}.$i.gz && mv {quoted}.$i.gz {quoted}.$((i+1)).gz || true; "
            "done"
        ),
        f"test -f {quoted} && gzip -c {quoted} > {quoted}.1.gz || true",
        f"cat > {quoted}.tmp",
        f"mv {quoted}.tmp {quoted}",
    ]
    subprocess.run(
        [*SSH, host, "; ".join(rotate)],
        input=local_ledger.read_bytes(),
        check=True,
    )
