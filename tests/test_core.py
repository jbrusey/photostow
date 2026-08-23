import os
from pathlib import Path

import pytest

from photostow import core
from photostow.core import (
    hash_tree,
    ledger_paths,
    missing_hash_records,
    parse_sha_lines,
    paths_not_in_ledger,
    prune_ledger_lines,
    sha256_file,
)


def test_sha256_file_rejects_missing_input(tmp_path: Path) -> None:
    path = tmp_path / "missing.jpg"
    with pytest.raises(ValueError) as error:
        sha256_file(path)
    assert str(error.value) == f"hash input does not exist: {path}"


def test_sha256_file_rejects_non_directory_parent(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.write_bytes(b"not a directory")
    path = parent / "photo.jpg"
    with pytest.raises(ValueError) as error:
        sha256_file(path)
    assert str(error.value) == f"hash input parent is not a directory: {parent}"


def test_sha256_file_rejects_non_regular_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        sha256_file(tmp_path)
    assert str(error.value) == f"refusing non-regular hash input: {tmp_path}"


def test_sha256_file_rejects_missing_no_follow(tmp_path: Path, monkeypatch) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    monkeypatch.delattr(core.os, "O_NOFOLLOW", raising=False)
    with pytest.raises(OSError) as error:
        sha256_file(photo)
    assert str(error.value) == "safe no-follow file opening is unavailable"


def test_sha256_file_rejects_symlink_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "photo.jpg").write_bytes(b"photo")
    parent = tmp_path / "parent"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError) as error:
        sha256_file(parent / "photo.jpg")
    assert str(error.value) == f"refusing symlink hash input parent: {parent}"


def test_sha256_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.jpg"
    target.write_bytes(b"target")
    link = tmp_path / "link.jpg"
    link.symlink_to(target)

    with pytest.raises(ValueError) as error:
        sha256_file(link)
    assert str(error.value) == f"refusing symlink hash input: {link}"


def test_sha256_file_rejects_path_identity_change(tmp_path: Path, monkeypatch) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"stable size")
    real_stat = core.os.stat

    def stat(path, *, follow_symlinks=True):
        result = real_stat(path, follow_symlinks=follow_symlinks)
        if path == photo:
            values = list(result)
            values[1] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(core.os, "stat", stat)
    with pytest.raises(ValueError) as error:
        sha256_file(photo)
    assert str(error.value) == f"hash input changed while reading: {photo}"


def test_sha256_file_rejects_metadata_change(tmp_path: Path, monkeypatch) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"stable size")
    real_fstat = core.os.fstat
    calls = 0

    def fstat(fd: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        result = real_fstat(fd)
        if calls == 2:
            values = list(result)
            values[8] += 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(core.os, "fstat", fstat)
    with pytest.raises(ValueError) as error:
        sha256_file(photo)
    assert str(error.value) == f"hash input changed while reading: {photo}"


def test_sha256_file(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"not really a jpeg")

    assert (
        sha256_file(photo)
        == "21ac2586e213d1f490778a07bf0025a98fc57595863a282372bac594b398322b"
    )


def test_parse_sha_lines_keeps_paths_with_spaces() -> None:
    lines = ["abc123  /tmp/my export/photo one.jpg\n"]

    assert list(parse_sha_lines(lines)) == [("abc123", "/tmp/my export/photo one.jpg")]


def test_missing_hash_records_compares_content_not_name() -> None:
    source = [
        "same  /export/new-name.jpg\n",
        "new  /export/actually-new.jpg\n",
    ]
    archive = ["same  /var/services/photo/old-name.jpg\n"]

    assert list(missing_hash_records(source, archive)) == [
        ("new", "/export/actually-new.jpg")
    ]


def test_hash_tree_rejects_invalid_roots(tmp_path: Path) -> None:
    for root in (tmp_path / "missing", tmp_path / "file.txt"):
        if root.name == "file.txt":
            root.write_bytes(b"file")
        with pytest.raises(NotADirectoryError):
            list(hash_tree(root))


def test_hash_tree_does_not_traverse_symlink_directories(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "photo.jpg").write_bytes(b"photo")
    (tmp_path / "linked").symlink_to(target, target_is_directory=True)

    assert list(hash_tree(tmp_path)) == [
        (sha256_file(target / "photo.jpg"), target / "photo.jpg")
    ]


def test_hash_tree_rejects_symlink_root(tmp_path: Path) -> None:
    target = tmp_path / "tree"
    target.mkdir()
    root = tmp_path / "link"
    root.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError) as error:
        list(hash_tree(root))
    assert str(error.value) == f"refusing symlink tree root: {root}"


def test_hash_tree_skips_symlink_files(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    (tmp_path / "alias.jpg").symlink_to(photo)

    assert list(hash_tree(tmp_path)) == [(sha256_file(photo), photo)]


def test_hash_tree_lists_files_sorted(tmp_path: Path) -> None:
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")

    assert [path.name for _, path in hash_tree(tmp_path)] == ["a.txt", "b.txt"]


def test_ledger_paths_uses_path_column() -> None:
    assert ledger_paths(["abc  /var/services/photo/a.jpg\n"]) == {
        "/var/services/photo/a.jpg"
    }


def test_paths_not_in_ledger_hashes_only_new_remote_paths() -> None:
    current = ["/var/services/photo/new.jpg", "/var/services/photo/old.jpg"]
    ledger = ["abc  /var/services/photo/old.jpg\n"]

    assert paths_not_in_ledger(current, ledger) == ["/var/services/photo/new.jpg"]


def test_prune_ledger_lines_keeps_only_current_paths_once() -> None:
    ledger = [
        "old  /gone.jpg\n",
        "keep  /keep.jpg\n",
        "dupe  /keep.jpg\n",
    ]

    assert prune_ledger_lines(ledger, {"/keep.jpg"}) == ["keep  /keep.jpg"]
