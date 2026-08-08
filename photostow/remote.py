from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from photostow.core import paths_not_in_ledger


def remote_find(host: str, root: str) -> list[str]:
    cmd = f"find {shlex.quote(root)} -type f -not -path '*/@eaDir/*' -print0"
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
