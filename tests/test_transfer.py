from pathlib import Path

from photostow.core import sha256_file
from photostow.transfer import HashedFile, plan_missing, scan_cached


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
