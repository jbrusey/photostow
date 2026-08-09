from pathlib import Path

from photostow.core import (
    hash_tree,
    ledger_paths,
    missing_hash_records,
    parse_sha_lines,
    paths_not_in_ledger,
    prune_ledger_lines,
    sha256_file,
)


def test_sha256_file(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"not really a jpeg")

    assert sha256_file(photo) == "21ac2586e213d1f490778a07bf0025a98fc57595863a282372bac594b398322b"


def test_parse_sha_lines_keeps_paths_with_spaces() -> None:
    lines = ["abc123  /tmp/my export/photo one.jpg\n"]

    assert list(parse_sha_lines(lines)) == [("abc123", "/tmp/my export/photo one.jpg")]


def test_missing_hash_records_compares_content_not_name() -> None:
    source = [
        "same  /export/new-name.jpg\n",
        "new  /export/actually-new.jpg\n",
    ]
    archive = ["same  /volume1/photo/old-name.jpg\n"]

    assert list(missing_hash_records(source, archive)) == [
        ("new", "/export/actually-new.jpg")
    ]


def test_hash_tree_lists_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    assert [path.name for _, path in hash_tree(tmp_path)] == ["a.txt", "b.txt"]


def test_ledger_paths_uses_path_column() -> None:
    assert ledger_paths(["abc  /volume1/photo/a.jpg\n"]) == {"/volume1/photo/a.jpg"}


def test_paths_not_in_ledger_hashes_only_new_remote_paths() -> None:
    current = ["/volume1/photo/new.jpg", "/volume1/photo/old.jpg"]
    ledger = ["abc  /volume1/photo/old.jpg\n"]

    assert paths_not_in_ledger(current, ledger) == ["/volume1/photo/new.jpg"]


def test_prune_ledger_lines_keeps_only_current_paths_once() -> None:
    ledger = [
        "old  /gone.jpg\n",
        "keep  /keep.jpg\n",
        "dupe  /keep.jpg\n",
    ]

    assert prune_ledger_lines(ledger, {"/keep.jpg"}) == ["keep  /keep.jpg"]
