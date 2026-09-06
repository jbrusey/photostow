from pathlib import Path

from photostow import transfer
from photostow.core import sha256_file
from photostow.transfer import (
    HashedFile,
    completed_transfers,
    plan_missing,
    record_transfer,
    scan_cached,
)


def test_transfer_batch_rsyncs_then_ingests_and_records(
    tmp_path: Path, monkeypatch
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "photo.jpg"
    source.write_bytes(b"photo")
    digest = "a" * 64
    record = HashedFile(source, digest, Path("2024/photo.jpg"))
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(transfer.subprocess, "run", run)
    state = tmp_path / "state.jsonl"
    assert (
        transfer.transfer_batch(
            [record],
            source_root,
            "oxygen",
            "/tmp/stage",
            "/var/services/photo",
            "/volume1/photostow",
            state,
            set(),
        )
        == 1
    )
    assert len(calls) == 2
    assert calls[0][0][0] == transfer.RSYNC
    assert calls[1][0][0] == transfer.SSH[0]
    assert (digest, "2024/photo.jpg") in transfer.completed_transfers(state)


def test_transfer_state_is_durable_and_reloadable(tmp_path: Path) -> None:
    state = tmp_path / "state.jsonl"
    digest = "a" * 64
    record_transfer(state, digest, Path("source.jpg"), Path("2024/source.jpg"))
    record_transfer(
        state, "b" * 64, Path("bad.jpg"), Path("2024/bad.jpg"), "failed", "network"
    )

    assert completed_transfers(state) == {(digest, "2024/source.jpg")}


def test_scan_cached_ignores_ds_store(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / ".DS_Store").write_bytes(b"metadata")
    photo = source / "photo.jpg"
    photo.write_bytes(b"photo")

    result = scan_cached(source, tmp_path / "cache.json")

    assert [record.path for record in result] == [photo]


def test_scan_cached_reuses_unchanged_hash(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    photo = source / "photo.jpg"
    photo.write_bytes(b"photo")
    cache = tmp_path / "cache.json"

    first = scan_cached(source, cache)
    assert first[0].digest == sha256_file(photo)

    def fail(_path: Path) -> str:
        raise AssertionError("unchanged file was rehashed")

    monkeypatch.setattr("photostow.transfer.sha256_file", fail)
    assert scan_cached(source, cache) == first


def test_scan_cached_reuses_hash_after_rename(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    original = source / "old.jpg"
    original.write_bytes(b"photo")
    cache = tmp_path / "cache.json"
    first = scan_cached(source, cache)
    original.rename(source / "new.jpg")

    def fail(_path: Path) -> str:
        raise AssertionError("renamed unchanged file was rehashed")

    monkeypatch.setattr("photostow.transfer.sha256_file", fail)
    result = scan_cached(source, cache)
    assert result[0].path.name == "new.jpg"
    assert result[0].digest == first[0].digest


def test_plan_missing_reports_duplicates_and_queues_one() -> None:
    duplicate = "a" * 64
    other = "b" * 64
    plan = plan_missing(
        [
            HashedFile(Path("z.jpg"), duplicate),
            HashedFile(Path("a.jpg"), duplicate),
            HashedFile(Path("other.jpg"), other),
        ],
        {other},
    )

    assert plan.files == (HashedFile(Path("a.jpg"), duplicate),)
    assert plan.duplicates == {duplicate: (Path("a.jpg"), Path("z.jpg"))}
