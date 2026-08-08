from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from pathlib import Path

from photostow.core import paths_not_in_ledger


def remote_find(host: str, root: str) -> list[str]:
    find_root = root.rstrip("/") + "/"
    cmd = f"find {shlex.quote(find_root)} -type f -not -path '*/@eaDir/*' -print0"
    result = subprocess.run(
        ["ssh", host, cmd], check=True, stdout=subprocess.PIPE, stderr=None
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def remote_sha256(host: str, paths: list[str]) -> str:
    if not paths:
        return ""
    data = "\0".join(paths).encode() + b"\0"
    result = subprocess.run(
        ["ssh", host, "xargs -0 sha256sum"],
        input=data,
        check=True,
        stdout=subprocess.PIPE,
        stderr=None,
    )
    return result.stdout.decode()


def paths_from_missing_tsv(tsv: Path) -> list[Path]:
    with tsv.open(encoding="utf-8") as f:
        header = next(f, "")
        if header.rstrip("\n").split("\t")[-1] != "path":
            raise ValueError("missing TSV must have path as the last column")
        return [Path(line.rstrip("\n").split("\t")[-1]) for line in f if line.strip()]


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
        subprocess.run(["ssh", host, mkdir], check=True)
        with subprocess.Popen(
            ["tar", "-cf", "-", "-C", str(source_root), "-T", f.name],
            env={**os.environ, "COPYFILE_DISABLE": "1"},
            stdout=subprocess.PIPE,
        ) as tar:
            subprocess.run(["ssh", host, extract], stdin=tar.stdout, check=True)
            if tar.stdout:
                tar.stdout.close()
            if tar.wait() != 0:
                raise subprocess.CalledProcessError(tar.returncode, tar.args)
    return len(paths)


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
