from pathlib import Path
from typing import Any

from photostow import remote


def test_paths_from_missing_tsv_and_relative_paths(tmp_path: Path) -> None:
    tsv = tmp_path / "missing.tsv"
    root = tmp_path / "originals"
    path = root / "A" / "photo.jpg"
    tsv.write_text(
        f"sha256\tcreated\tadjusted\tpath\nabc\t2024-01-01T00:00:00\t0\t{path}\n",
        encoding="utf-8",
    )

    records = remote.missing_records(tsv)
    paths = remote.paths_from_missing_tsv(tsv)

    assert records[0].year == "2024"
    assert paths == [path]
    assert remote.relative_paths(paths, root) == ["A/photo.jpg"]


def test_stage_by_year_hardlinks_flat_year_dirs(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    src = root / "A" / "photo.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"photo")
    stage = tmp_path / "stage"
    record = remote.MissingRecord("abc123", "2025-02-03T00:00:00", False, src)

    assert remote.stage_by_year([record], root, stage) == 1
    assert (stage / "2025" / "photo.jpg").read_bytes() == b"photo"


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
