from pathlib import Path
from typing import Any

from photostow import remote


def test_update_remote_ledger_hashes_only_new_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = tmp_path / "photos-oxygen-sha"
    ledger.write_text("oldhash  /volume1/photo/old.jpg\n", encoding="utf-8")

    monkeypatch.setattr(
        remote,
        "remote_find",
        lambda host, root: ["/volume1/photo/old.jpg", "/volume1/photo/new.jpg"],
    )
    hashed: list[list[str]] = []

    def fake_sha256(host: str, paths: list[str]) -> str:
        hashed.append(paths)
        return "newhash  /volume1/photo/new.jpg\n"

    monkeypatch.setattr(remote, "remote_sha256", fake_sha256)

    assert remote.update_remote_ledger("oxygen", "/volume1/photo", ledger) == 1
    assert hashed == [["/volume1/photo/new.jpg"]]
    assert ledger.read_text(encoding="utf-8") == (
        "oldhash  /volume1/photo/old.jpg\nnewhash  /volume1/photo/new.jpg\n"
    )
