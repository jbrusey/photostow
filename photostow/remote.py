from __future__ import annotations

import os
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
TAR_PROGRESS = ["--checkpoint=10000", "--checkpoint-action=dot"] if TAR[0].endswith("gtar") else []


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
    return result.stdout.decode()


def remote_find(host: str, root: str) -> list[str]:
    find_root = root.rstrip("/") + "/"
    cmd = (
        f"find {shlex.quote(find_root)} -path '*/@eaDir' -prune -o "
        "-name 'photos-oxygen-sha*' -prune -o -type f -print0"
    )
    result = subprocess.run(
        [*SSH, host, cmd], check=True, stdout=subprocess.PIPE, stderr=None
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def remote_sha256(host: str, paths: list[str]) -> str:
    if not paths:
        return ""
    data = "\0".join(paths).encode() + b"\0"
    result = subprocess.run(
        [*SSH, host, "xargs -0 sha256sum"],
        input=data,
        check=True,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    return result.stdout.decode()


def missing_records(tsv: Path) -> list[MissingRecord]:
    with tsv.open(encoding="utf-8") as f:
        header = next(f, "").rstrip("\n").split("\t")
        if header != ["sha256", "created", "adjusted", "path"]:
            raise ValueError("missing TSV must have columns: sha256, created, adjusted, path")
        rows = []
        for line in f:
            if not line.strip():
                continue
            digest, created, adjusted, path = line.rstrip("\n").split("\t")
            rows.append(MissingRecord(digest, created, adjusted == "1", Path(path)))
        return rows


def paths_from_missing_tsv(tsv: Path) -> list[Path]:
    return [record.path for record in missing_records(tsv)]


def relative_paths(paths: list[Path], root: Path) -> list[str]:
    return [str(path.relative_to(root)) for path in paths]


def copy_paths_tar(paths: list[str], source_root: Path, host: str, dest_root: str) -> int:
    if not paths:
        return 0
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as f:
        f.write("\n".join(paths) + "\n")
        f.flush()
        mkdir = f"mkdir -p {shlex.quote(dest_root)}"
        extract = f"cd {shlex.quote(dest_root)} && tar -xf -"
        subprocess.run([*SSH, host, mkdir], check=True)
        with subprocess.Popen(
            [*TAR, *TAR_PROGRESS, "-cf", "-", "-C", str(source_root), "-T", f.name],
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
    for record in records:
        rel = record.path.relative_to(source_root)
        src = source_root / rel
        if not src.is_file():
            continue
        name = src.name
        dest = stage / record.year / name
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
    subprocess.run([*SSH, host, f"mkdir -p {shlex.quote(dest_root)}"], check=True)
    years = sorted(path.name for path in stage.iterdir() if path.is_dir())
    files = [str(path.relative_to(stage)) for path in stage.rglob("*") if path.is_file()]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as f:
        f.write("\n".join(files) + "\n")
        f.flush()
        print(f"copying {len(files)} files", file=sys.stderr)
        tar_cmd = [*TAR, *TAR_PROGRESS, "-cf", "-", "-C", str(stage), "-T", f.name]
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


def prune_remote_ledger(host: str, root: str, ledger: Path, output: Path | None = None) -> tuple[int, int]:
    old = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    current = set(remote_find(host, root))
    pruned = prune_ledger_lines(old, current)
    target = output or ledger
    target.write_text("\n".join(pruned) + ("\n" if pruned else ""), encoding="utf-8")
    return len(old), len(pruned)


def update_remote_ledger(host: str, root: str, ledger: Path, output: Path | None = None) -> int:
    old = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    new_paths = paths_not_in_ledger(remote_find(host, root), old)
    new_hashes = remote_sha256(host, new_paths)
    target = output or ledger
    text = "\n".join(old)
    if text and new_hashes:
        text += "\n"
    text += new_hashes.rstrip("\n")
    if text:
        text += "\n"
    target.write_text(text, encoding="utf-8")
    return len(new_paths)


def install_remote_ledger(host: str, local_ledger: Path, remote_ledger: str, keep: int = 5) -> None:
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
