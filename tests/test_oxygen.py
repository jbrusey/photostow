from __future__ import annotations

import fcntl
import hashlib
import os
from pathlib import Path

import pytest

from photostow import oxygen
from photostow.oxygen import (
    _lock_path,
    gc_candidates,
    gc_summary,
    ingest,
    migrate,
    object_path,
    verify_objects,
    visible_files,
)
from photostow.oxygen_cli import main as migrate_main
from photostow.oxygen_gc_cli import main as gc_main
from photostow.oxygen_ingest_cli import main as ingest_main
from photostow.oxygen_verify_cli import main as verify_main


def test_hardlink_preflight_cleans_up_second_probe_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_mkstemp = oxygen.tempfile.mkstemp
    calls = 0

    def fail_second_probe(*, dir):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("probe failed")
        return real_mkstemp(dir=dir)

    monkeypatch.setattr(oxygen.tempfile, "mkstemp", fail_second_probe)
    with pytest.raises(OSError, match="probe failed"):
        oxygen._check_hardlink_support(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_check_resources_rejects_no_free_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Usage:
        free = 0

    monkeypatch.setattr(oxygen.shutil, "disk_usage", lambda path: Usage())

    with pytest.raises(OSError, match="no free bytes"):
        oxygen._check_resources(tmp_path, 1)


def test_check_resources_rejects_inode_exhaustion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Usage:
        free = 1

    monkeypatch.setattr(oxygen.shutil, "disk_usage", lambda path: Usage())
    monkeypatch.setattr(
        oxygen.os,
        "statvfs",
        lambda path: type("Stat", (), {"f_favail": 0})(),
    )

    with pytest.raises(OSError, match="not enough free inodes"):
        oxygen._check_resources(tmp_path, 1)


def test_visible_files_is_sorted_and_excludes_metadata(tmp_path: Path) -> None:
    (tmp_path / "b.jpg").write_bytes(b"b")
    (tmp_path / "a.jpg").write_bytes(b"a")
    (tmp_path / ".DS_Store").write_bytes(b"x")
    (tmp_path / ".afpDeleted3500075").write_bytes(b"x")
    (tmp_path / "@eaDir").mkdir()
    (tmp_path / "@eaDir" / "indexed.jpg").write_bytes(b"x")
    (tmp_path / "._DAV").mkdir()
    (tmp_path / "._DAV" / "metadata").write_bytes(b"x")
    (tmp_path / ".objects").mkdir()
    (tmp_path / ".photostow.lock").write_text("", encoding="utf-8")
    (tmp_path / "photos-oxygen-sha").write_bytes(b"ledger")
    (tmp_path / ".objects" / "object").write_bytes(b"x")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / ".DS_Store").write_bytes(b"x")
    (nested / ".afpDeleted3500076").write_bytes(b"x")
    (nested / ".photostow.lock").write_bytes(b"x")
    (nested / "photos-oxygen-sha.1.gz").write_bytes(b"x")
    (nested / ".objects").mkdir()
    (nested / ".objects" / "object").write_bytes(b"x")
    (nested / "@eaDir").mkdir()
    (nested / "@eaDir" / "indexed.jpg").write_bytes(b"x")
    (nested / "._DAV").mkdir()
    (nested / "._DAV" / "metadata").write_bytes(b"x")

    assert [path.name for path in visible_files(tmp_path)] == ["a.jpg", "b.jpg"]
    assert [path.name for path in visible_files(tmp_path, exclude=("",))] == [
        "a.jpg",
        "b.jpg",
    ]
    assert [path.name for path in visible_files(tmp_path, exclude=("   ",))] == [
        "a.jpg",
        "b.jpg",
    ]
    assert [
        path.name for path in visible_files(tmp_path, exclude=("photos-*", "photos-*"))
    ] == [
        "a.jpg",
        "b.jpg",
    ]


def test_visible_files_path_separator_pattern_does_not_match_basename(
    tmp_path: Path,
) -> None:
    photo_dir = tmp_path / "sub"
    photo_dir.mkdir()
    photo = photo_dir / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, exclude=("sub/*.jpg",)) == [photo]
    assert visible_files(tmp_path, exclude=(r"sub\*.jpg",)) == [photo]
    assert visible_files(tmp_path, exclude=("sub/[p]hoto.jpg",)) == [photo]


def test_visible_files_rejects_invalid_exclude_container(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="exclude must be a tuple"):
        visible_files(tmp_path, exclude=["*.tmp"])  # type: ignore[arg-type]


def test_visible_files_rejects_invalid_flags(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="progress and verbose must be booleans"):
        visible_files(tmp_path, progress=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="progress and verbose must be booleans"):
        visible_files(tmp_path, verbose="yes")  # type: ignore[arg-type]


def test_visible_files_rejects_invalid_root_and_selection(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="root must be a Path"):
        visible_files(str(tmp_path))  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="root must be a Path"):
        visible_files(b"archive")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="selected must be a Path"):
        visible_files(tmp_path, selected="selected")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="selected must be a Path"):
        visible_files(tmp_path, selected=b"selected")  # type: ignore[arg-type]


def test_visible_files_rejects_invalid_excluded_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="excluded root must be a Path"):
        visible_files(tmp_path, excluded_root="excluded")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="excluded root must be a Path"):
        visible_files(tmp_path, excluded_root=b"excluded")  # type: ignore[arg-type]


def test_visible_files_rejects_non_string_exclude_pattern(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exclude patterns must be strings"):
        visible_files(tmp_path, exclude=(None,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="exclude patterns must be strings"):
        visible_files(tmp_path, exclude=(b"*.jpg",))  # type: ignore[arg-type]


def test_visible_files_rejects_symlinked_excluded_root_parent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(root, excluded_root=parent / "objects")


def test_visible_files_rejects_normalized_missing_selection(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        visible_files(tmp_path, selected=tmp_path / "missing" / "." / "child")


def test_visible_files_normalizes_parent_paths(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert (
        visible_files(
            tmp_path / "." / "child" / "..",
            selected=tmp_path / "." / "nested" / "..",
            excluded_root=tmp_path / "." / "excluded" / "..",
        )
        == []
    )


def test_visible_files_resolves_relative_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    selected = root / "selected"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    (root / "excluded").mkdir()
    monkeypatch.chdir(tmp_path)

    assert visible_files(
        Path("root"),
        selected=Path("root/selected"),
        excluded_root=Path("root/excluded"),
    ) == [photo]


def test_visible_files_does_not_confuse_path_prefixes(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"excluded")
    sibling = tmp_path / "selected-other"
    sibling.mkdir()
    photo = sibling / "photo.jpg"
    photo.write_bytes(b"kept")

    assert visible_files(tmp_path, selected=sibling, excluded_root=selected) == [photo]


def test_visible_files_excludes_selected_subdirectory(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")
    assert visible_files(tmp_path, selected=selected, excluded_root=selected) == []

    (selected / "nested").mkdir()
    (selected / "nested" / "photo.jpg").write_bytes(b"photo")
    assert (
        visible_files(tmp_path, selected=selected / "nested", excluded_root=selected)
        == []
    )


def test_visible_files_excludes_discovery_root(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, excluded_root=tmp_path) == []


def test_visible_files_prunes_overlapping_excluded_subtree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    keep = root / "keep.jpg"
    keep.write_bytes(b"keep")
    excluded = root / "excluded"
    (excluded / "@eaDir").mkdir(parents=True)
    (excluded / "@eaDir" / "indexed.jpg").write_bytes(b"index")
    (excluded / ".objects").mkdir()
    (excluded / "photo.jpg").write_bytes(b"photo")

    assert visible_files(root, excluded_root=excluded) == [keep]
    assert visible_files(root, excluded_root=excluded, exclude=("*.tmp",)) == [keep]


def test_visible_files_accepts_normalized_missing_excluded_root(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(
        tmp_path, excluded_root=tmp_path / "missing" / "objects" / ".."
    ) == [photo]


def test_visible_files_accepts_missing_external_excluded_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    photo = root / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(root, excluded_root=tmp_path / "objects") == [photo]


def test_visible_files_accepts_relative_external_excluded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    photo = root / "photo.jpg"
    photo.write_bytes(b"photo")
    (tmp_path / "objects").mkdir()
    monkeypatch.chdir(tmp_path)

    assert visible_files(Path("root"), excluded_root=Path("objects//../objects")) == [
        photo
    ]
    assert visible_files(Path("root"), excluded_root=Path("objects///")) == [photo]


def test_visible_files_accepts_external_excluded_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    photo = root / "photo.jpg"
    photo.write_bytes(b"photo")
    excluded = tmp_path / "objects"
    excluded.mkdir()

    assert visible_files(root, excluded_root=excluded) == [photo]


def test_visible_files_rejects_file_excluded_root(tmp_path: Path) -> None:
    excluded = tmp_path / "excluded"
    excluded.write_bytes(b"not a directory")

    with pytest.raises(NotADirectoryError):
        visible_files(tmp_path, excluded_root=excluded)


def test_visible_files_rejects_dangling_excluded_root_parent_with_separator(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-target", target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(tmp_path, excluded_root=Path(str(link / "objects") + "/"))


def test_visible_files_rejects_excluded_root_symlink_parent_with_separator(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(tmp_path, excluded_root=Path(str(link / "objects") + "/"))


def test_visible_files_rejects_excluded_root_symlink_with_separator(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root is a symlink"):
        visible_files(tmp_path, excluded_root=Path(str(link) + "/"))


def test_visible_files_excluded_root_normalizes_dot_and_separator(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    selected = excluded / "object"
    selected.write_bytes(b"object")

    assert (
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=Path(str(excluded / ".") + "///"),
        )
        == []
    )


def test_visible_files_excluded_root_normalizes_alias_and_separator(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    selected = excluded / "object"
    selected.write_bytes(b"object")

    assert (
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=Path(str(tmp_path / "container" / ".." / "objects") + "///"),
        )
        == []
    )


def test_visible_files_rejects_dangling_excluded_root_alias(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-root", target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(tmp_path, excluded_root=link / ".." / "objects")


def test_visible_files_rejects_symlink_excluded_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    excluded = root / "excluded"
    excluded.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root is a symlink"):
        visible_files(root, excluded_root=excluded)


def test_visible_files_rejects_dangling_root_alias_symlink_component(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-root", target_is_directory=True)

    with pytest.raises(ValueError, match="root has symlinked parent"):
        visible_files(link / ".." / "archive")


def test_visible_files_rejects_dangling_symlink_root_parent(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-root", target_is_directory=True)

    with pytest.raises(ValueError, match="root has symlinked parent"):
        visible_files(link / "archive")


def test_visible_files_rejects_nested_symlinked_root_parent(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-root", target_is_directory=True)

    with pytest.raises(ValueError, match="root has symlinked parent"):
        visible_files(link / "nested" / "archive")


def test_visible_files_rejects_dangling_symlink_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.symlink_to(tmp_path / "missing-root", target_is_directory=True)

    with pytest.raises(ValueError, match="root is a symlink"):
        visible_files(root)


def test_visible_files_propagates_root_link_check_error_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve

    def failing_is_symlink(path: Path) -> bool:
        if path == tmp_path:
            raise PermissionError("root link check denied")
        return original_is_symlink(path)

    def unexpected_resolve(path: Path, *args, **kwargs) -> Path:
        if path == tmp_path:
            raise AssertionError("root resolution must follow link checks")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    with pytest.raises(PermissionError, match="root link check denied"):
        visible_files(tmp_path)


def test_visible_files_rejects_symlink_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    root = tmp_path / "root"
    root.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="root is a symlink"):
        visible_files(root)


def test_visible_files_rejects_symlink_selection(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    link = root / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="selection is a symlink"):
        visible_files(root, selected=link)


def test_visible_files_rejects_dangling_symlink_directory_selection(
    tmp_path: Path,
) -> None:
    link = tmp_path / "missing-album"
    link.symlink_to(tmp_path / "target-album", target_is_directory=True)

    with pytest.raises(ValueError, match="selection is a symlink"):
        visible_files(tmp_path, selected=link)


def test_visible_files_rejects_dangling_symlink_selection(tmp_path: Path) -> None:
    link = tmp_path / "missing.jpg"
    link.symlink_to(tmp_path / "target.jpg")

    with pytest.raises(ValueError, match="selection is a symlink"):
        visible_files(tmp_path, selected=link)


def test_visible_files_rejects_dangling_symlink_selection_parent(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(tmp_path / "missing-album", target_is_directory=True)

    with pytest.raises(ValueError, match="selection has symlinked parent"):
        visible_files(tmp_path, selected=link / "photo.jpg")


def test_visible_files_rejects_nested_symlinked_selection_parent(
    tmp_path: Path,
) -> None:
    link = tmp_path / "linked-album"
    link.symlink_to(tmp_path / "missing-album", target_is_directory=True)
    selected = link / "nested" / "photo.jpg"

    with pytest.raises(ValueError, match="selection has symlinked parent"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_propagates_selected_link_check_error_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    original_is_symlink = Path.is_symlink
    original_resolve = Path.resolve

    def failing_is_symlink(path: Path) -> bool:
        if path == selected:
            raise PermissionError("selection link check denied")
        return original_is_symlink(path)

    def unexpected_resolve(path: Path, *args, **kwargs) -> Path:
        if path == selected:
            raise AssertionError("selection resolution must follow link checks")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    with pytest.raises(PermissionError, match="selection link check denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_rejects_selected_link_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "photo.jpg"
    target.write_bytes(b"photo")
    selected = tmp_path / "selected.jpg"
    selected.symlink_to(target)
    original = Path.resolve

    def unexpected_resolve(path: Path, *args, **kwargs) -> Path:
        if path == selected:
            raise AssertionError("selected resolution must follow link checks")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    with pytest.raises(ValueError, match="selection is a symlink"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_rejects_selected_parent_before_resolve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    parent = tmp_path / "parent"
    parent.symlink_to(target, target_is_directory=True)
    selected = parent / "photo.jpg"

    original = Path.resolve

    def unexpected_resolve(path: Path, *args, **kwargs) -> Path:
        if path == selected:
            raise AssertionError("selected resolution must follow parent link checks")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", unexpected_resolve)
    with pytest.raises(ValueError, match="selection has symlinked parent"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_rejects_symlink_selection_parent(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "target"
    target.mkdir()
    (target / "file.jpg").write_bytes(b"photo")
    link = root / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="selection has symlinked parent"):
        visible_files(root, selected=link / "file.jpg")


def test_visible_files_sorts_after_symlink_filtering(tmp_path: Path) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-target.jpg"
    target.write_bytes(b"linked")
    (tmp_path / "b.jpg").write_bytes(b"b")
    (tmp_path / "a.jpg").write_bytes(b"a")
    (tmp_path / "linked.jpg").symlink_to(target)

    assert [path.name for path in visible_files(tmp_path)] == ["a.jpg", "b.jpg"]


def test_visible_files_prunes_selected_reserved_directory_with_verbose(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / "@eaDir"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, progress=True, verbose=True) == []
    assert capsys.readouterr().out == ""


def test_visible_files_prunes_selected_reserved_directory_with_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    selected = tmp_path / ".objects"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, progress=True) == []
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("name", [".objects", "@eaDir"])
def test_visible_files_prunes_selected_reserved_directory(
    tmp_path: Path, name: str
) -> None:
    selected = tmp_path / name
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected) == []


def test_visible_files_preserves_unicode_alias_for_selected_directory_external_root(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = tmp_path / unicodedata.normalize("NFD", external.name)

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_excludes_nested_unicode_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    (selected / "photo.jpg").write_bytes(b"photo")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, excluded_root=alias) == []


def test_visible_files_prunes_only_nested_unicode_excluded_subtree(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    siblings = [external / "keep", external / "keep-too"]
    excluded.mkdir(parents=True)
    for sibling in siblings:
        sibling.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = [sibling / "visible.jpg" for sibling in siblings]
    for photo in visible:
        photo.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, excluded_root=alias) == visible


def test_visible_files_keeps_reserved_siblings_excluded_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    sibling = external / "keep"
    excluded.mkdir(parents=True)
    sibling.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (sibling / "visible.jpg").write_bytes(b"visible")
    (sibling / ".objects").mkdir()
    (sibling / ".objects" / "metadata.jpg").write_bytes(b"metadata")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, excluded_root=alias) == [sibling / "visible.jpg"]


def test_visible_files_keeps_reserved_files_excluded_beside_unicode_subtree(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    excluded.mkdir(parents=True)
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = external / "visible.jpg"
    visible.write_bytes(b"visible")
    (external / ".DS_Store").write_bytes(b"metadata")
    (external / ".photostow.lock").write_bytes(b"metadata")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, excluded_root=alias) == [visible]


def test_visible_files_keeps_pattern_neighbors_excluded_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    sibling = external / "keep"
    excluded.mkdir(parents=True)
    sibling.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = sibling / "visible.jpg"
    visible.write_bytes(b"visible")
    (sibling / "cache.tmp").write_bytes(b"cache")
    (sibling / "thumb.bak").write_bytes(b"thumb")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("*.tmp", "*.bak"), excluded_root=alias) == [
        visible
    ]


def test_visible_files_prunes_pattern_directories_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    patterned = external / "cache"
    excluded.mkdir(parents=True)
    patterned.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (patterned / "cached.jpg").write_bytes(b"cached")
    visible = external / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("cache",), excluded_root=alias) == [visible]


def test_visible_files_prunes_nested_pattern_directories_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    patterned = external / "keep" / "cache"
    peer = external / "keep" / "other"
    excluded.mkdir(parents=True)
    patterned.mkdir(parents=True)
    peer.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (patterned / "cached.jpg").write_bytes(b"cached")
    visible = peer / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("cache",), excluded_root=alias) == [visible]


def test_visible_files_prunes_unicode_pattern_directories_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    patterned = external / "café-cache"
    excluded.mkdir(parents=True)
    patterned.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (patterned / "cached.jpg").write_bytes(b"cached")
    visible = external / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_case_sensitive_unicode_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "Café-cache"
    nonmatching = external / "cafe-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    nonmatching.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matching.jpg").write_bytes(b"matching")
    visible = nonmatching / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("Café-*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_basename_unicode_wildcards_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    sibling = external / "keep"
    excluded.mkdir(parents=True)
    sibling.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (sibling / "café-cache").mkdir()
    (sibling / "café-cache" / "nested.jpg").write_bytes(b"nested")
    visible = sibling / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_keeps_separator_wildcards_basename_only_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    sibling = external / "keep"
    excluded.mkdir(parents=True)
    sibling.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    nested = sibling / "cache"
    nested.mkdir()
    cached = nested / "cached.jpg"
    cached.write_bytes(b"cached")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("keep/*",), excluded_root=alias) == [cached]


def test_visible_files_uses_unicode_character_classes_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-cache"
    nonmatching = external / "cafe-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    nonmatching.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = nonmatching / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("caf[é]-*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_negated_unicode_character_classes_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-cache"
    nonmatching = external / "cafe-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    nonmatching.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = nonmatching / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("caf[!é]-*",), excluded_root=alias) == [
        matching / "matched.jpg"
    ]


def test_visible_files_matches_literal_wildcards_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    literal = external / "café[1]"
    ordinary = external / "café1"
    excluded.mkdir(parents=True)
    literal.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (literal / "literal.jpg").write_bytes(b"literal")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café[[]1]",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_matches_literal_brackets_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    literal = external / "café[é]"
    ordinary = external / "caféé"
    excluded.mkdir(parents=True)
    literal.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (literal / "literal.jpg").write_bytes(b"literal")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café[[]é]",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_scopes_malformed_unicode_patterns_to_basenames(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    malformed = external / "café["
    ordinary = external / "café1"
    excluded.mkdir(parents=True)
    malformed.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (malformed / "malformed.jpg").write_bytes(b"malformed")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café[",), excluded_root=alias) == [visible]


def test_visible_files_ignores_empty_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    excluded.mkdir(parents=True)
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = external / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("",), excluded_root=alias) == [visible]


def test_visible_files_uses_literal_whitespace_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café *",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_preserves_spaces_in_unicode_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    spaced = external / " café "
    ordinary = external / "café"
    excluded.mkdir(parents=True)
    spaced.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (spaced / "spaced.jpg").write_bytes(b"spaced")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=(" café ",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_tab_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\tcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\t*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_newline_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\ncache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\n*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_carriage_return_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\rcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\r*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_form_feed_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\fcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\f*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_vertical_tab_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\vcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\v*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_mixed_control_whitespace_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café\t\rcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café\t\r*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_unicode_punctuation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café—cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café—*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-📷"
    ordinary = external / "café-photo"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-📷*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_combining_mark_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "cafe\u0301-cache"
    ordinary = external / "cafe-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("cafe\u0301-*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_variation_selector_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-☕️"
    ordinary = external / "café-☕"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-☕️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_zero_width_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\u200bcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\u200b*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_bidi_control_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\u202ecache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\u202e*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_private_use_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\ue000cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\ue000*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_noncharacter_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\ufdd0*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_supplementary_plane_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-𝄞cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-𝄞*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_mathematical_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-∞cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-∞*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_modifier_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-²cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-²*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_enclosed_alphanumeric_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-①cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-①*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_currency_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-€cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-€*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_letterlike_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-ℓcache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-ℓ*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_geometric_shape_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-◆cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-◆*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_dingbat_patterns_with_unicode_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-✂cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-✂*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_musical_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-♫cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-♫*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_weather_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-☁cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-☁*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_map_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🗺cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🗺*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_arrow_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-→cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-→*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_decorative_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-✦cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-✦*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_modifier_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👍🏽cache"
    ordinary = external / "café-👍cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👍🏽*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_zwj_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👨‍👩‍👧‍👦*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_flag_sequence_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸cache"
    ordinary = external / "café-cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🇺🇸*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_tag_sequence_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    excluded.mkdir(parents=True)
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    visible = external / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-\U000e0067*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_combining_emoji_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-1️⃣cache"
    ordinary = external / "café-1cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-1️⃣*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_keycap_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-2️⃣cache"
    ordinary = external / "café-2cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-2️⃣*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_text_emoji_presentation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-™️cache"
    ordinary = external / "café-™cache"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-™️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_skin_tone_zwj_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩🏽‍💻cache"
    ordinary = external / "café-developer"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👩🏽‍💻*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_gendered_emoji_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩‍⚕️cache"
    ordinary = external / "café-doctor"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👩‍⚕️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_hair_style_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍🦰cache"
    ordinary = external / "café-person"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑‍🦰*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_family_role_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍🚀cache"
    ordinary = external / "café-astronaut"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑‍🚀*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_couple_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍🤝‍🧑cache"
    ordinary = external / "café-couple"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑‍🤝‍🧑*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_family_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👪cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👪*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_regional_modifier_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸🏽cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🇺🇸🏽*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_combining_mark_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-☝️cache"
    ordinary = external / "café-hand"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-☝️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_selector_modifier_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-☝️🏽cache"
    ordinary = external / "café-hand"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-☝️🏽*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_emoji_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🏴\U000e0067\U000e0062\U000e007f-cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🏴\U000e0067\U000e0062\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_digit_keycap_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-1️⃣cache"
    ordinary = external / "café-number"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-1️⃣*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_enclosed_alphanumeric_double_circle_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-⓵cache"
    ordinary = external / "café-number"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-⓵*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_double_circle_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-◉cache"
    ordinary = external / "café-circle"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-◉*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_double_arrow_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-➡cache"
    ordinary = external / "café-direction"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-➡*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_black_diamond_dingbat_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-❖cache"
    ordinary = external / "café-dingbat"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-❖*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_g_clef_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-𝄞cache"
    ordinary = external / "café-music"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-𝄞*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_snowman_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-☃cache"
    ordinary = external / "café-weather"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-☃*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_transport_symbol_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🚂cache"
    ordinary = external / "café-travel"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🚂*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_compass_map_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧭cache"
    ordinary = external / "café-map"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧭*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_bitcoin_currency_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-₿cache"
    ordinary = external / "café-money"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-₿*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_prescription_sign_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-℞cache"
    ordinary = external / "café-letter"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-℞*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_subscript_nine_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-₉cache"
    ordinary = external / "café-number"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-₉*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_supplementary_noncharacter_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\U0001fffe-cache"
    ordinary = external / "café-noncharacter"
    excluded.mkdir(parents=True)
    try:
        matching.mkdir()
    except OSError as exc:
        pytest.skip(f"filesystem rejects supplementary noncharacters: {exc}")
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-\U0001fffe-*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_supplementary_private_use_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\U000f0000-cache"
    ordinary = external / "café-private"
    excluded.mkdir(parents=True)
    try:
        matching.mkdir()
    except OSError as exc:
        pytest.skip(f"filesystem rejects supplementary private-use symbols: {exc}")
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-\U000f0000-*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_bidirectional_control_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\u202ecache"
    ordinary = external / "café-direction"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\u202e*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_vertical_form_control_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\x0b\x0ccache"
    ordinary = external / "café-control"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-\x0b\x0c*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_zero_width_non_joiner_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-\u200ccache"
    ordinary = external / "café-zero-width"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-\u200c*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_interrobang_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-‽cache"
    ordinary = external / "café-punctuation"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-‽*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_supplementary_math_pi_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-𝛑cache"
    ordinary = external / "café-math"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-𝛑*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_decorative_star_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-✺cache"
    ordinary = external / "café-decoration"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-✺*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_paired_shape_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-◉◆cache"
    ordinary = external / "café-shapes"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-◉◆*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_gear_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-⚙️cache"
    ordinary = external / "café-symbol"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-⚙️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_symbol_combining_mark_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-✦\u0301cache"
    ordinary = external / "café-symbol"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-✦\u0301*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_punctuation_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-‽\u0301cache"
    ordinary = external / "café-punctuation"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-‽\u0301*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_currency_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-₿\u0301cache"
    ordinary = external / "café-currency"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-₿\u0301*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_letterlike_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-℞\u0301cache"
    ordinary = external / "café-letter"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-℞\u0301*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_shape_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-◉️cache"
    ordinary = external / "café-shape"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-◉️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_shape_combining_selector_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-◉\u0301️cache"
    ordinary = external / "café-shape"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-◉\u0301️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-✦\u0301\u0323cache"
    ordinary = external / "café-symbol"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-✦\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_selector_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-⚙️\u0301\u0323cache"
    ordinary = external / "café-symbol"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-⚙️\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_tag_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🏴️cache"
    ordinary = external / "café-tag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🏴️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_flag_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸\U000e0067\U000e007f-cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🇺🇸\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_couple_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👫️cache"
    ordinary = external / "café-couple"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👫️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_family_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦️cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👨‍👩‍👧‍👦️*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_flag_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸️cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🇺🇸️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_modifier_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👍🏻\u0301cache"
    ordinary = external / "café-hand"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👍🏻\u0301*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_zwj_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩‍💻️cache"
    ordinary = external / "café-zwj"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👩‍💻️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_zwj_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩‍💻\u0301\u0323cache"
    ordinary = external / "café-zwj"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👩‍💻\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_modifier_zwj_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩🏻‍💻\u0301cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👩🏻‍💻\u0301*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_flag_zwj_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸‍🏳️cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🇺🇸‍🏳️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩‍💻\U000e0067\U000e007f-cache"
    ordinary = external / "café-zwj"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👩‍💻\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_modifier_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👍🏻\U000e0067\U000e007f-cache"
    ordinary = external / "café-hand"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👍🏻\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_family_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦\U000e0067\U000e007f-cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👨‍👩‍👧‍👦\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_couple_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👫\U000e0067\U000e007f-cache"
    ordinary = external / "café-couple"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👫\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_family_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦\U000e0067\U000e007f-cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👨‍👩‍👧‍👦\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_modifier_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩🏻‍💻\U000e0067\U000e007f-cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👩🏻‍💻\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_keycap_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-1️⃣\u0301cache"
    ordinary = external / "café-number"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-1️⃣\u0301*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_flag_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸\u0301\u0323cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🇺🇸\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_tag_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🏴\U000e0067\U000e007f\u0301\u0323-cache"
    ordinary = external / "café-tag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🏴\U000e0067\U000e007f\u0301\u0323-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_flag_zwj_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸‍🏳️\u0301cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🇺🇸‍🏳️\u0301*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_family_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦\u0301\u0323cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-👨‍👩‍👧‍👦\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_flag_tag_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸\U000e0067\U000e007f\u0301\u0323-cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🇺🇸\U000e0067\U000e007f\u0301\u0323-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_flag_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸‍🏳️\U000e0067\U000e007f-cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🇺🇸‍🏳️\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_flag_zwj_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🇺🇸‍🏳️\u0301\u0323cache"
    ordinary = external / "café-flag"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🇺🇸‍🏳️\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_couple_zwj_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👫‍️cache"
    ordinary = external / "café-couple"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-👫‍️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_family_zwj_combining_selector_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👨‍👩‍👧‍👦️\u0301\u0323cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👨‍👩‍👧‍👦️\u0301\u0323*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_family_modifier_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-👩🏻‍💻\U000e0067\U000e007f-cache"
    ordinary = external / "café-family"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-👩🏻‍💻\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍⚕️\U000e0067\U000e007f-cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑‍⚕️\U000e0067\U000e007f-*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍⚕️️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑‍⚕️️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_role_multiple_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍⚕️\u0301\u0323cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑‍⚕️\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_zwj_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑‍⚕️️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑‍⚕️️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_role_modifier_zwj_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻‍⚕️\u0301\u0323cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑🏻‍⚕️\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_modifier_zwj_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻‍⚕️️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑🏻‍⚕️️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_role_modifier_zwj_combining_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻‍⚕️\u0301\u0323️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑🏻‍⚕️\u0301\u0323️*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑🏻🏼‍⚕️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_role_two_modifiers_zwj_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(tmp_path, exclude=("café-🧑🏻🏼‍⚕️️*",), excluded_root=alias) == [
        visible
    ]


def test_visible_files_uses_role_two_modifiers_zwj_combining_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\u0301\u0323cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑🏻🏼‍⚕️\u0301\u0323*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_combining_variation_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\u0301\u0323️cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑🏻🏼‍⚕️\u0301\u0323️*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_tag_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\U000e0100cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path, exclude=("café-🧑🏻🏼‍⚕️\U000e0100*",), excluded_root=alias
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_tags_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\U000e0100\U000e0101cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑🏻🏼‍⚕️\U000e0100\U000e0101*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_combining_tags_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\u0301\u0323\U000e0100\U000e0101cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑🏻🏼‍⚕️\u0301\u0323\U000e0100\U000e0101*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_variation_tags_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️️\U000e0100\U000e0101cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑🏻🏼‍⚕️️\U000e0100\U000e0101*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_combining_variation_tags_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\u0301\u0323️\U000e0100\U000e0101cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑🏻🏼‍⚕️\u0301\u0323️\U000e0100\U000e0101*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_role_two_modifiers_zwj_combining_variation_tags_multiple_patterns_with_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "café"
    excluded = external / "album"
    matching = external / "café-🧑🏻🏼‍⚕️\u0301\u0323️\U000e0100\U000e0101cache"
    ordinary = external / "café-role"
    excluded.mkdir(parents=True)
    matching.mkdir()
    ordinary.mkdir()
    (excluded / "hidden.jpg").write_bytes(b"hidden")
    (matching / "matched.jpg").write_bytes(b"matched")
    visible = ordinary / "visible.jpg"
    visible.write_bytes(b"visible")
    alias = Path(str(external) + "/.//album/")

    assert visible_files(
        tmp_path,
        exclude=("café-🧑🏻🏼‍⚕️\u0301\u0323️\U000e0100\U000e0101*",),
        excluded_root=alias,
    ) == [visible]


def test_visible_files_uses_host_case_for_selected_directory_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    case_alias = Path(str(external).swapcase())
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, excluded_root=case_alias) == [
        selected / "photo.jpg"
    ]


def test_visible_files_preserves_selected_directory_for_missing_unicode_external_root(
    tmp_path: Path,
) -> None:
    import unicodedata

    selected = tmp_path / "café" / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    missing = tmp_path / unicodedata.normalize("NFD", "café-missing")

    assert visible_files(tmp_path, selected=selected, excluded_root=missing) == [photo]


def test_visible_files_preserves_selected_directory_for_missing_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(
        tmp_path,
        selected=selected,
        excluded_root=tmp_path / "missing-external",
    ) == [photo]


def test_visible_files_rejects_file_external_root_for_selected_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    external = tmp_path / "external-file"
    external.write_bytes(b"not a directory")

    with pytest.raises(NotADirectoryError):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_propagates_external_root_check_error_for_selected_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == external:
            raise PermissionError("external selection exclusion check denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(
        PermissionError, match="external selection exclusion check denied"
    ):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_rejects_dangling_external_root_parent_for_selected_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    link = tmp_path / "missing-parent"
    link.symlink_to(tmp_path / "missing-target", target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=link / "external",
        )


def test_visible_files_rejects_symlinked_external_root_parent_for_selected_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    selected = target / "external" / "album"
    selected.mkdir(parents=True)
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=link / "external",
        )


def test_visible_files_rejects_symlinked_external_root_for_selected_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    selected = target / "album"
    selected.mkdir(parents=True)
    excluded = tmp_path / "external"
    excluded.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root is a symlink"):
        visible_files(tmp_path, selected=selected, excluded_root=excluded)


def test_visible_files_excludes_selected_directory_with_external_root_parent_alias(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    alias = tmp_path / "missing-parent" / ".." / "external"

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_excludes_selected_directory_with_external_root_redundant_separator(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    alias = Path(str(tmp_path) + "//external")

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_excludes_selected_directory_with_external_root_separator(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    (selected / "photo.jpg").write_bytes(b"photo")
    external_alias = Path(str(external) + "/")

    assert (
        visible_files(tmp_path, selected=selected, excluded_root=external_alias) == []
    )


def test_visible_files_excludes_selected_directory_with_combined_relative_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=Path("external") / "." / ".." / "external",
        )
        == []
    )


def test_visible_files_excludes_selected_directory_with_mixed_relative_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=Path("external//") / "." / ".." / "external/",
        )
        == []
    )


def test_visible_files_excludes_selected_directory_with_relative_external_dot_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(tmp_path, selected=selected, excluded_root=Path(".") / "external")
        == []
    )


def test_visible_files_excludes_selected_directory_with_relative_external_redundant_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(tmp_path, selected=selected, excluded_root=Path("external//"))
        == []
    )


def test_visible_files_excludes_selected_directory_with_relative_external_separator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(tmp_path, selected=selected, excluded_root=Path("external/"))
        == []
    )


def test_visible_files_preserves_selected_directory_for_missing_relative_parent_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    monkeypatch.chdir(tmp_path)

    assert visible_files(
        tmp_path,
        selected=selected,
        excluded_root=Path("missing-parent") / ".." / "missing-external",
    ) == [photo]


def test_visible_files_preserves_selected_directory_for_missing_relative_external_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    monkeypatch.chdir(tmp_path)

    assert visible_files(
        tmp_path, selected=selected, excluded_root=Path("missing-external")
    ) == [photo]


def test_visible_files_excludes_unicode_combined_parent_separator_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    nfd = unicodedata.normalize("NFD", external.name)
    alias = tmp_path / nfd / "//.." / external.name / "//"

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_preserves_unicode_combined_dot_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "//./"
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_preserves_unicode_combined_redundant_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "//./"
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_preserves_unicode_dot_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "/."
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_preserves_unicode_redundant_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "//"
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_excludes_mixed_unicode_dot_parent_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    nfd = unicodedata.normalize("NFD", external.name)
    alias = tmp_path / nfd / "." / ".." / external.name

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_excludes_unicode_parent_separator_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    nfd = unicodedata.normalize("NFD", external.name)
    alias = tmp_path / nfd / ".." / external.name / "."

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_excludes_mixed_unicode_parent_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    nfd = unicodedata.normalize("NFD", external.name)
    alias = tmp_path / nfd / ".." / external.name

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == []


def test_visible_files_preserves_unicode_trailing_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "//."
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_preserves_combined_unicode_external_alias_for_selected_directory(
    tmp_path: Path,
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    alias = Path(
        str(tmp_path) + "/" + unicodedata.normalize("NFD", external.name) + "//."
    )

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_preserves_relative_unicode_external_alias_for_selected_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import unicodedata

    external = tmp_path / "café"
    selected = external / "album"
    selected.mkdir(parents=True)
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")
    monkeypatch.chdir(tmp_path)
    alias = Path(unicodedata.normalize("NFD", external.name))

    assert visible_files(tmp_path, selected=selected, excluded_root=alias) == [photo]


def test_visible_files_excludes_selected_directory_with_relative_external_parent_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(
            tmp_path,
            selected=selected,
            excluded_root=Path("parent") / ".." / "external",
        )
        == []
    )


def test_visible_files_excludes_selected_directory_with_relative_external_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    (selected / "photo.jpg").write_bytes(b"photo")
    monkeypatch.chdir(tmp_path)

    assert (
        visible_files(tmp_path, selected=selected, excluded_root=Path("external")) == []
    )


def test_visible_files_excludes_normalized_selected_directory_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    (selected / "photo.jpg").write_bytes(b"photo")
    selected_alias = external / "nested" / ".." / "album"
    external_alias = tmp_path / "archive" / ".." / "external"

    assert (
        visible_files(tmp_path, selected=selected_alias, excluded_root=external_alias)
        == []
    )


def test_visible_files_excludes_redundant_alias_selected_directory_beneath_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    selected_alias = external / "nested" / ".." / "album"
    excluded_alias = Path(str(external) + "//")

    assert (
        visible_files(tmp_path, selected=selected_alias, excluded_root=excluded_alias)
        == []
    )


def test_visible_files_excludes_trailing_alias_selected_directory_beneath_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    selected_alias = external / "nested" / ".." / "album"
    excluded_alias = Path(str(external) + "/")

    assert (
        visible_files(tmp_path, selected=selected_alias, excluded_root=excluded_alias)
        == []
    )


def test_visible_files_excludes_nested_alias_selected_directory_beneath_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    alias = external / "nested" / ".." / "album"

    assert visible_files(tmp_path, selected=alias, excluded_root=external) == []


def test_visible_files_excludes_selected_directory_beneath_external_root(
    tmp_path: Path,
) -> None:
    external = tmp_path / "external"
    selected = external / "album"
    selected.mkdir(parents=True)
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, excluded_root=external) == []


def test_visible_files_prunes_selected_directory_parent_alias(
    tmp_path: Path,
) -> None:
    (tmp_path / ".objects").mkdir()
    alias = tmp_path / ".objects" / ".." / ".objects"

    assert visible_files(tmp_path, selected=alias) == []


def test_visible_files_prunes_redundant_separator_selected_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / ".objects"
    selected.mkdir()
    alias = Path(str(tmp_path) + "//.objects")

    assert visible_files(tmp_path, selected=alias) == []


def test_visible_files_prunes_trailing_separator_selected_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / ".objects"
    selected.mkdir()
    with_separator = Path(str(selected) + "/")

    assert visible_files(tmp_path, selected=with_separator) == []


def test_visible_files_prunes_normalized_selected_reserved_directory(
    tmp_path: Path,
) -> None:
    (tmp_path / ".objects").mkdir()
    alias = tmp_path / "nested" / ".." / ".objects"

    assert visible_files(tmp_path, selected=alias) == []


def test_visible_files_prunes_selected_directory_by_pattern(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "private-album"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, exclude=("private-*",)) == []


def test_visible_files_propagates_selected_directory_file_check_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    original = Path.is_file

    def failing_is_file(path: Path) -> bool:
        if path == selected:
            raise PermissionError("selected directory file check denied")
        return original(path)

    monkeypatch.setattr(Path, "is_file", failing_is_file)
    with pytest.raises(PermissionError, match="selected directory file check denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_rejects_missing_selected_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "missing-album"

    with pytest.raises(FileNotFoundError):
        visible_files(tmp_path, selected=selected)


def test_visible_files_rejects_selected_directory_symlink_parent(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "linked"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="selection has symlinked parent"):
        visible_files(tmp_path, selected=link / "album")


def test_visible_files_rejects_symlinked_selected_directory(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "selected"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="selection is a symlink"):
        visible_files(tmp_path, selected=link)


def test_visible_files_excludes_selected_directory_before_traversal(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, excluded_root=selected) == []


def test_visible_files_selected_directory_never_returns_directory(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    (selected / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, selected=selected, exclude=("*",)) == []


@pytest.mark.parametrize("name", [".DS_Store", ".photostow.lock"])
def test_visible_files_filters_selected_metadata_with_wildcard(
    tmp_path: Path, name: str
) -> None:
    selected = tmp_path / name
    selected.write_bytes(b"metadata")

    assert visible_files(tmp_path, selected=selected, exclude=("*",)) == []


def test_visible_files_filters_matching_ledger_files(
    tmp_path: Path,
) -> None:
    (tmp_path / "photos-oxygen-sha.1").write_bytes(b"ledger")
    kept = tmp_path / "photo.jpg"
    kept.write_bytes(b"photo")

    assert visible_files(tmp_path, exclude=("photos-oxygen-sha*",)) == [kept]


def test_visible_files_filters_matching_symlink_files(
    tmp_path: Path,
) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-target.jpg"
    target.write_bytes(b"linked")
    (tmp_path / "linked.jpg").symlink_to(target)
    (tmp_path / "kept.jpg").write_bytes(b"kept")

    assert visible_files(tmp_path, exclude=("linked*",)) == [tmp_path / "kept.jpg"]


def test_visible_files_does_not_follow_symlink_directories(tmp_path: Path) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-target"
    target.mkdir()
    (target / "nested.jpg").write_bytes(b"nested")
    (tmp_path / "linked").symlink_to(target, target_is_directory=True)
    regular = tmp_path / "regular.jpg"
    regular.write_bytes(b"regular")

    assert visible_files(tmp_path) == [regular]


def test_visible_files_excludes_directory_symlink_without_traversing(
    tmp_path: Path,
) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-excluded-target"
    target.mkdir()
    (target / "nested.jpg").write_bytes(b"nested")
    (tmp_path / "linked").symlink_to(target, target_is_directory=True)
    regular = tmp_path / "regular.jpg"
    regular.write_bytes(b"regular")

    assert visible_files(tmp_path, exclude=("linked",)) == [regular]


def test_visible_files_excludes_dangling_symlink_by_name(tmp_path: Path) -> None:
    (tmp_path / "skip.jpg").symlink_to(tmp_path / "missing.jpg")

    assert visible_files(tmp_path, exclude=("*.jpg",)) == []


def test_visible_files_excludes_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.jpg"
    target.write_bytes(b"target")
    (tmp_path / "link.jpg").symlink_to(target)

    assert visible_files(tmp_path) == [target]
    assert visible_files(tmp_path, exclude=("*.jpg",)) == []


def test_migrate_rejects_missing_nofollow_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"photo")
    monkeypatch.delattr("photostow.oxygen.os.O_NOFOLLOW")

    with pytest.raises(OSError, match="no-follow"):
        migrate(tmp_path)


def test_migrate_rejects_file_changed_during_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    monkeypatch.setattr(
        "photostow.oxygen._stable_digest",
        lambda path: (_ for _ in ()).throw(OSError("file changed while hashing")),
    )
    with pytest.raises(OSError, match="changed while hashing"):
        migrate(tmp_path)


def test_visible_files_propagates_walk_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_walk(path: Path, onerror=None):
        if onerror:
            onerror(PermissionError("denied"))
        return iter(())

    monkeypatch.setattr("photostow.oxygen.os.walk", failing_walk)
    with pytest.raises(PermissionError, match="denied"):
        visible_files(tmp_path)


def test_visible_files_rejects_missing_root_or_selection(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        visible_files(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        visible_files(tmp_path, tmp_path / "missing")


def test_visible_files_rejects_selection_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="beneath root"):
        visible_files(tmp_path, tmp_path.parent)


def test_migrate_dry_run_discovers_before_applying_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.jpg").write_bytes(b"a")
    (tmp_path / "b.jpg").write_bytes(b"b")
    discovered = False

    def walk(path: Path, onerror=None):
        nonlocal discovered
        yield str(path), [], ["a.jpg", "b.jpg"]
        discovered = True

    monkeypatch.setattr(oxygen.os, "walk", walk)
    original_digest = oxygen._stable_digest

    def digest(path: Path):
        assert discovered
        return original_digest(path)

    monkeypatch.setattr(oxygen, "_stable_digest", digest)
    assert migrate(tmp_path, dry_run=True, limit=1) == 1


def test_migrate_reports_hash_progress_before_each_hash(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"photo")

    assert migrate(tmp_path, limit=1, verbose=True) == 1
    output = capsys.readouterr().out
    assert "discovering" in output
    assert "hashing 1/1" in output
    assert not (tmp_path / ".photostow.lock").exists()
    assert not (tmp_path / ".objects").exists()


def test_verify_objects_rejects_symlink_root_parent(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(target, target_is_directory=True)

    assert verify_objects(parent / "objects") == [
        f"object root parent is a symlink: {parent}"
    ]


def test_verify_objects_rejects_symlink_root(tmp_path: Path) -> None:
    real = tmp_path / "objects"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    assert "object root is a symlink" in verify_objects(link)[0]


def test_verify_objects_reports_symlink_shard(tmp_path: Path) -> None:
    target = tmp_path / "real-shard"
    target.mkdir()
    shard = tmp_path / "sha256" / "aa"
    shard.parent.mkdir()
    shard.symlink_to(target, target_is_directory=True)

    assert verify_objects(tmp_path) == [f"symlink object path: {shard}"]


def test_verify_objects_rejects_symlink_store(tmp_path: Path) -> None:
    target = tmp_path / "real-store"
    target.mkdir()
    store = tmp_path / "sha256"
    store.symlink_to(target, target_is_directory=True)

    assert verify_objects(tmp_path) == [f"object store is a symlink: {store}"]


def test_verify_objects_reports_symlink_entries(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    target = tmp_path / "outside"
    target.write_bytes(b"outside")
    link = root / ".objects" / "sha256" / "aa" / ("a" * 62)
    link.parent.mkdir(parents=True)
    link.symlink_to(target)

    malformed_dir = root / ".objects" / "sha256" / "not-a-shard"
    malformed_dir.mkdir()
    errors = verify_objects(root / ".objects")
    assert any("symlink object path" in error for error in errors)
    assert any("malformed object path" in error for error in errors)


def test_gc_retains_objects_with_visible_hardlinks(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    object_root = root / ".objects"
    digest = __import__("hashlib").sha256(b"photo").hexdigest()
    object_file = object_path(root, digest)
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"photo")
    __import__("os").link(object_file, root / "visible.jpg")

    assert gc_summary(object_root) == ([], 1)


def test_gc_reports_only_unlinked_verified_objects(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    object_root = root / ".objects"
    digest = __import__("hashlib").sha256(b"orphan").hexdigest()
    orphan = object_path(root, digest)
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")

    assert gc_candidates(object_root) == [orphan]
    assert gc_summary(object_root) == ([orphan], 0)


def test_verify_objects_reports_corruption_and_malformed_paths(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    migrate(tmp_path, dry_run=False)
    digest = __import__("hashlib").sha256(b"photo").hexdigest()
    object_file = object_path(tmp_path, digest)
    object_file.write_bytes(b"corrupt")
    malformed = tmp_path / ".objects" / "sha256" / "bad" / "name"
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"x")

    errors = verify_objects(tmp_path / ".objects")
    assert any("content mismatch" in error for error in errors)
    assert any("malformed object path" in error for error in errors)


def test_migrate_cli_uses_default_root(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = []
    monkeypatch.setattr(
        "sys.argv", ["oxygen-migrate", "2006", "--dry-run", "--nice", "0"]
    )

    def fake_migrate(root: Path, **kwargs: object) -> int:
        calls.append((root, kwargs))
        return 0

    monkeypatch.setattr("photostow.oxygen_cli.migrate", fake_migrate)

    assert migrate_main() == 0
    assert calls[0][0] == Path("/var/services/photo")
    assert calls[0][1]["selected"] == Path("/var/services/photo/2006").resolve()
    captured = capsys.readouterr()
    output = captured.out
    assert output.startswith("oxygen-migrate mode=dry-run")
    assert f"root={Path('/var/services/photo').resolve()}" in output
    assert captured.err.startswith("would migrate 0 files elapsed=")
    assert "rate=0.00/s" in captured.err
    assert f"object-root={Path('/volume1/photostow').resolve()}" in output


def test_migrate_cli_supports_root_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-migrate", "2006", "--root", str(tmp_path), "--nice", "0"],
    )

    def fake_migrate(root: Path, **kwargs: object) -> int:
        calls.append((root, kwargs))
        return 0

    monkeypatch.setattr("photostow.oxygen_cli.migrate", fake_migrate)

    assert migrate_main() == 0
    assert calls[0][0] == tmp_path
    assert calls[0][1]["selected"] == tmp_path / "2006"


def test_migrate_cli_reports_resolved_startup_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-migrate",
            "2006",
            "--root",
            "archive",
            "--object-root",
            "objects",
            "--nice",
            "0",
        ],
    )
    monkeypatch.setattr("photostow.oxygen_cli.migrate", lambda *args, **kwargs: 0)

    assert migrate_main() == 0
    output = capsys.readouterr().out
    assert f"root={(tmp_path / 'archive').resolve()}" in output
    assert f"object-root={(tmp_path / 'objects').resolve()}" in output
    assert "target=2006 path=." in output


def test_migrate_cli_reports_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", str(tmp_path), "--nice", "0"])
    monkeypatch.setattr(
        "photostow.oxygen_cli.migrate",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert migrate_main() == 130
    assert "oxygen-migrate interrupted; no completion summary" in (
        capsys.readouterr().err
    )


def test_migrate_cli_rejects_symlinked_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "photo.jpg").write_bytes(b"outside")
    target = root / "2006"
    target.symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(
        "sys.argv", ["oxygen-migrate", "2006", "--root", str(root), "--nice", "0"]
    )

    assert migrate_main() == 1
    assert "oxygen-migrate failed:" in capsys.readouterr().err


def test_migrate_cli_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-migrate",
            "2006",
            "--path",
            "../../outside",
            "--root",
            str(tmp_path),
            "--nice",
            "0",
        ],
    )

    assert migrate_main() == 1
    assert "oxygen-migrate failed:" in capsys.readouterr().err


def test_migrate_cli_composes_target_and_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-migrate", "2006", "--path", "photo.jpg", "--nice", "0"],
    )

    def fake_migrate(root: Path, **kwargs: object) -> int:
        calls.append((root, kwargs))
        return 0

    monkeypatch.setattr("photostow.oxygen_cli.migrate", fake_migrate)
    assert migrate_main() == 0
    assert (
        calls[0][1]["selected"] == Path("/var/services/photo/2006/photo.jpg").resolve()
    )


def test_migrate_cli_passes_exclusions_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-migrate",
            str(tmp_path),
            "--nice",
            "0",
            "--exclude",
            "Photos",
            "--exclude",
            ".objects",
        ],
    )

    def fake_migrate(root: Path, **kwargs: object) -> int:
        calls.append(kwargs["exclude"])
        return 0

    monkeypatch.setattr("photostow.oxygen_cli.migrate", fake_migrate)

    assert migrate_main() == 0
    assert calls == [("Photos", ".objects")]


def test_migrate_cli_rejects_negative_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", str(tmp_path), "--limit", "-1"])
    with pytest.raises(SystemExit) as error:
        migrate_main()
    assert error.value.code == 2


def test_migrate_cli_rejects_corrupt_existing_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    photo = tmp_path / "2006" / "photo.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo")
    digest = hashlib.sha256(b"photo").hexdigest()
    object_file = object_path(tmp_path, digest)
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"corrupt")
    monkeypatch.setattr(
        "sys.argv", ["oxygen-migrate", "2006", "--root", str(tmp_path), "--nice", "0"]
    )

    assert migrate_main() == 1
    assert object_file.read_bytes() == b"corrupt"
    assert "oxygen-migrate failed" in capsys.readouterr().err


def test_migrate_cli_rejects_symlink_existing_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    photo = tmp_path / "2006" / "photo.jpg"
    photo.parent.mkdir()
    photo.write_bytes(b"photo")
    digest = hashlib.sha256(b"photo").hexdigest()
    object_file = object_path(tmp_path, digest)
    object_file.parent.mkdir(parents=True)
    target = tmp_path / "external-object"
    target.write_bytes(b"photo")
    object_file.symlink_to(target)
    monkeypatch.setattr(
        "sys.argv", ["oxygen-migrate", "2006", "--root", str(tmp_path), "--nice", "0"]
    )

    assert migrate_main() == 1
    assert object_file.is_symlink()
    assert "oxygen-migrate failed" in capsys.readouterr().err


def test_migrate_cli_rejects_symlink_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real-objects"
    real.mkdir()
    object_root = tmp_path / ".objects"
    object_root.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", "2006", "--root", str(tmp_path)])

    assert migrate_main() == 1
    assert "must not be a symlink" in capsys.readouterr().err


def test_migrate_cli_reports_migration_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", str(tmp_path)])
    monkeypatch.setattr(
        "photostow.oxygen_cli.migrate",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("broken")),
    )

    assert migrate_main() == 1
    assert "oxygen-migrate failed: broken" in capsys.readouterr().err


def test_migrate_cli_rejects_unsupported_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", str(tmp_path), "--jobs", "2"])
    with pytest.raises(SystemExit) as error:
        migrate_main()
    assert error.value.code == 2


def test_migrate_cli_reports_nice_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-migrate", str(tmp_path), "--nice", "10"])
    monkeypatch.setattr(
        "photostow.oxygen_cli.os.nice",
        lambda value: (_ for _ in ()).throw(OSError("denied")),
    )

    assert migrate_main() == 2
    assert "could not apply --nice 10: denied" in capsys.readouterr().err


def test_verify_limit_bounds_namespace_checks(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    object_root = root / ".objects"
    digest = __import__("hashlib").sha256(b"valid").hexdigest()
    valid = object_path(root, digest)
    valid.parent.mkdir(parents=True)
    valid.write_bytes(b"valid")
    (object_root / "sha256" / "zz").mkdir()

    selected = valid.relative_to(object_root / "sha256")
    assert verify_objects(object_root, selected=selected, limit=1) == []


def test_verify_rejects_boolean_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, limit=True)
    assert str(error.value) == "limit must be a nonnegative integer"


def test_verify_rejects_negative_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, limit=-1)
    assert str(error.value) == "limit must not be negative"


def test_verify_selected_rejects_nul_path(tmp_path: Path) -> None:
    (tmp_path / "sha256").mkdir()
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, selected=Path("bad\x00path"))
    assert str(error.value) == "verification path must not contain NUL"


def test_verify_selected_rejects_empty_path(tmp_path: Path) -> None:
    (tmp_path / "sha256").mkdir()
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, selected=Path("  "))
    assert str(error.value) == "verification path must not be empty"


def test_verify_selected_rejects_path_escape(tmp_path: Path) -> None:
    (tmp_path / "sha256").mkdir()
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, selected=Path("../outside"))
    assert str(error.value) == "verification path must be beneath object store"


def test_verify_selected_rejects_absolute_path(tmp_path: Path) -> None:
    (tmp_path / "sha256").mkdir()
    with pytest.raises(ValueError) as error:
        verify_objects(tmp_path, selected=tmp_path / "object")
    assert str(error.value) == "verification path must be relative"


def test_verify_selected_rejects_symlink_parent(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    target = object_root / "sha256" / "aa"
    target.mkdir(parents=True)
    parent_target = target / "real"
    parent_target.mkdir()
    link = target / "linked"
    link.symlink_to(parent_target, target_is_directory=True)

    with pytest.raises(ValueError, match="verification path has symlinked parent"):
        verify_objects(object_root, selected=Path("aa/linked/file"))


def test_verify_selected_rejects_symlink(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    store = object_root / "sha256" / "aa"
    store.mkdir(parents=True)
    target = store / "target"
    target.write_bytes(b"target")
    link = store / "link"
    link.symlink_to(target)

    with pytest.raises(ValueError, match="verification path is a symlink"):
        verify_objects(object_root, selected=Path("aa/link"))


def test_verify_objects_supports_limit_and_progress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    (root / "a.jpg").write_bytes(b"a")
    (root / "b.jpg").write_bytes(b"b")
    migrate(root, dry_run=False)

    assert len(verify_objects(root / ".objects", limit=1, progress=True)) == 0
    assert "verifying 1/1" in capsys.readouterr().out


def test_gc_rejects_different_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "photostow.oxygen._device", lambda path: 1 if path == tmp_path else 2
    )
    with pytest.raises(ValueError, match="same filesystem"):
        gc_summary(tmp_path / "objects", root=tmp_path)


def test_gc_summary_rejects_malformed_object_file(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    object_file = object_root / "sha256" / "aa" / "not-a-digest"
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"invalid")

    with pytest.raises(OSError, match="malformed object path"):
        gc_summary(object_root)
    assert object_file.read_bytes() == b"invalid"


def test_gc_summary_rejects_symlink_object_store(tmp_path: Path) -> None:
    target = tmp_path / "real-store"
    target.mkdir()
    object_root = tmp_path / "objects"
    object_root.mkdir()
    (object_root / "sha256").symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="object store is a symlink"):
        gc_summary(object_root)


def test_gc_summary_rejects_symlink_object_shard(tmp_path: Path) -> None:
    target = tmp_path / "real-shard"
    target.mkdir()
    object_root = tmp_path / "objects"
    shard = object_root / "sha256" / "aa"
    shard.parent.mkdir(parents=True)
    shard.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="symlink object path"):
        gc_summary(object_root)


def test_gc_rejects_malformed_object_file(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    object_file = object_root / "sha256" / "aa" / "not-a-digest"
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"invalid")

    with pytest.raises(OSError, match="malformed object path"):
        gc_candidates(object_root)
    assert object_file.read_bytes() == b"invalid"


def test_gc_rejects_symlink_object_file(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"outside")
    object_root = tmp_path / "objects"
    object_file = object_root / "sha256" / "aa" / ("b" * 62)
    object_file.parent.mkdir(parents=True)
    object_file.symlink_to(target)

    with pytest.raises(OSError, match="symlink object path"):
        gc_candidates(object_root)
    assert target.read_bytes() == b"outside"


def test_gc_rejects_symlink_object_shard(tmp_path: Path) -> None:
    target = tmp_path / "real-shard"
    target.mkdir()
    object_root = tmp_path / "objects"
    shard = object_root / "sha256" / "aa"
    shard.parent.mkdir(parents=True)
    shard.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="symlink object path"):
        gc_candidates(object_root)


def test_gc_rejects_symlink_object_store(tmp_path: Path) -> None:
    target = tmp_path / "real-store"
    target.mkdir()
    object_root = tmp_path / "objects"
    object_root.mkdir()
    (object_root / "sha256").symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="object store is a symlink"):
        gc_candidates(object_root)


def test_gc_rejects_symlink_object_root_parent(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="object root parent must not be a symlink"):
        gc_candidates(parent / "objects")


def test_gc_summary_rejects_symlink_object_root_parent(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="object root parent must not be a symlink"):
        gc_summary(parent / "objects")


def test_gc_candidates_respects_operation_lock(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    with _lock_path(tmp_path).open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(OSError, match="another Oxygen operation"):
            gc_candidates(object_root, root=tmp_path)


def test_verify_cli_reports_resolved_root_and_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / "objects"
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(object_root)])
    monkeypatch.setattr(
        "photostow.oxygen_verify_cli.verify_objects", lambda *args, **kwargs: []
    )

    assert verify_main() == 0
    captured = capsys.readouterr()
    output = captured.out
    assert captured.err == ""
    assert f"root={object_root.resolve()}" in output
    assert "verified True errors=0 elapsed=" in output


def test_gc_cli_reports_explicit_root_for_custom_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / "objects"
    monkeypatch.setattr(
        "sys.argv", ["oxygen-gc", str(object_root), "--root", str(tmp_path)]
    )
    calls = []

    def fake_gc_summary(object_root: Path, *, root: Path) -> tuple[list[Path], int]:
        calls.append((object_root, root))
        return [], 0

    monkeypatch.setattr("photostow.oxygen_gc_cli.gc_summary", fake_gc_summary)

    assert gc_main() == 0
    assert calls == [(object_root, tmp_path)]
    assert f"root={tmp_path.resolve()}" in capsys.readouterr().out


def test_gc_cli_infers_root_for_dot_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / ".objects"
    digest = hashlib.sha256(b"orphan").hexdigest()
    orphan = object_root / "sha256" / digest[:2] / digest[2:]
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"orphan")
    monkeypatch.setattr("sys.argv", ["oxygen-gc", str(object_root)])

    assert gc_main() == 0
    captured = capsys.readouterr()
    output = captured.out
    assert captured.err == ""
    assert f"object-root={object_root.resolve()}" in output
    assert "candidates=1 retained=0" in output


def test_gc_cli_requires_root_for_custom_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-gc", str(tmp_path / "objects")])
    with pytest.raises(SystemExit) as error:
        gc_main()
    assert error.value.code == 2


def test_gc_cli_requires_object_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-gc"])
    with pytest.raises(SystemExit) as error:
        gc_main()
    assert error.value.code == 2


def test_gc_cli_reports_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-gc", str(tmp_path / "objects"), "--root", str(tmp_path)],
    )
    monkeypatch.setattr(
        "photostow.oxygen_gc_cli.gc_summary",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert gc_main() == 130
    assert "oxygen-gc interrupted; report incomplete" in capsys.readouterr().err


def test_gc_cli_reports_corrupt_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / "objects"
    digest = "a" * 64
    object_file = object_root / "sha256" / "aa" / digest[2:]
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"corrupt")
    monkeypatch.setattr(
        "sys.argv", ["oxygen-gc", str(object_root), "--root", str(tmp_path)]
    )

    assert gc_main() == 1
    captured = capsys.readouterr()
    assert "oxygen-gc mode=report" in captured.out
    assert "oxygen-gc failed: object store verification failed: content mismatch:" in (
        captured.err
    )


def test_verify_cli_rejects_symlinked_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = tmp_path / "sha256" / "aa"
    store.mkdir(parents=True)
    external = tmp_path / "external-object"
    external.write_bytes(b"outside")
    selected = store / "link"
    selected.symlink_to(external)
    monkeypatch.setattr(
        "sys.argv", ["oxygen-verify", str(tmp_path), "--path", "aa/link"]
    )

    assert verify_main() == 1
    assert "verification path must be beneath object store" in (capsys.readouterr().err)


def test_verify_cli_rejects_path_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / "objects"
    (object_root / "sha256").mkdir(parents=True)
    monkeypatch.setattr(
        "sys.argv", ["oxygen-verify", str(object_root), "--path", "../../outside"]
    )

    assert verify_main() == 1
    assert "verification path must be beneath object store" in (capsys.readouterr().err)


def test_verify_cli_rejects_symlink_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real = tmp_path / "real-objects"
    real.mkdir()
    object_root = tmp_path / "objects"
    object_root.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(object_root)])

    assert verify_main() == 1
    assert "object root is a symlink" in capsys.readouterr().err


def test_verify_cli_rejects_file_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    object_root = tmp_path / "objects"
    object_root.write_bytes(b"not a directory")
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(object_root)])

    assert verify_main() == 1
    assert "missing object store" in capsys.readouterr().err


def test_verify_cli_reports_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path)])
    monkeypatch.setattr(
        "photostow.oxygen_verify_cli.verify_objects",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert verify_main() == 130
    assert (
        "oxygen-verify interrupted; verification incomplete" in capsys.readouterr().err
    )


def test_verify_cli_rejects_negative_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path), "--limit", "-1"])
    with pytest.raises(SystemExit) as error:
        verify_main()
    assert error.value.code == 2


def test_verify_cli_passes_relative_path_to_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_verify(
        object_root: Path, selected: Path | None, limit: int | None, *, progress: bool
    ) -> list[str]:
        calls.append((object_root, selected, limit, progress))
        return []

    monkeypatch.setattr(
        "sys.argv", ["oxygen-verify", str(tmp_path), "--path", "shard/file"]
    )
    monkeypatch.setattr("photostow.oxygen_verify_cli.verify_objects", fake_verify)

    assert verify_main() == 0
    assert calls == [(tmp_path, Path("shard/file"), None, False)]


def test_verify_cli_passes_verbose_to_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_verify(
        object_root: Path, selected: Path | None, limit: int | None, *, progress: bool
    ) -> list[str]:
        calls.append(progress)
        return []

    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path), "--verbose"])
    monkeypatch.setattr("photostow.oxygen_verify_cli.verify_objects", fake_verify)

    assert verify_main() == 0
    assert calls == [True]


def test_verify_cli_passes_limit_to_verifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_verify(
        object_root: Path, selected: Path | None, limit: int | None, *, progress: bool
    ) -> list[str]:
        calls.append(limit)
        return []

    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path), "--limit", "7"])
    monkeypatch.setattr("photostow.oxygen_verify_cli.verify_objects", fake_verify)

    assert verify_main() == 0
    assert calls == [7]


def test_verify_cli_returns_failure_for_missing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path / "objects")])

    assert verify_main() == 1
    captured = capsys.readouterr()
    assert "errors=1" in captured.out
    assert "missing object store" in captured.err


def test_verify_cli_reports_corrupt_error_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    digest = "a" * 64
    object_file = tmp_path / "sha256" / "aa" / digest[2:]
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"corrupt")
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path)])

    assert verify_main() == 1
    captured = capsys.readouterr()
    assert "oxygen-verify root=" in captured.out
    assert "verified False errors=1" in captured.out
    assert "content mismatch" in captured.err


def test_verify_cli_limits_corruption_to_selected_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selected_digest = "a" * 64
    other_digest = "b" * 64
    for digest in (selected_digest, other_digest):
        object_file = tmp_path / "sha256" / digest[:2] / digest[2:]
        object_file.parent.mkdir(parents=True, exist_ok=True)
        object_file.write_bytes(b"corrupt")
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-verify", str(tmp_path), "--path", f"aa/{selected_digest[2:]}"],
    )

    assert verify_main() == 1
    captured = capsys.readouterr()
    assert "verified False errors=1" in captured.out
    assert f"aa/{selected_digest[2:]}" in captured.err
    assert f"bb/{other_digest[2:]}" not in captured.err


def test_verify_cli_reports_malformed_object_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    malformed = tmp_path / "sha256" / "bad" / "name"
    malformed.parent.mkdir(parents=True)
    malformed.write_bytes(b"bad")
    monkeypatch.setattr("sys.argv", ["oxygen-verify", str(tmp_path)])

    assert verify_main() == 1
    assert "malformed object path" in capsys.readouterr().err


def test_verify_objects_accepts_valid_object(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    migrate(tmp_path, dry_run=False)

    assert verify_objects(tmp_path / ".objects") == []


def test_ingest_rejects_symlink_object_root(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    real = tmp_path / "real-objects"
    real.mkdir()
    link = root / "objects"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        ingest(source, root / "photo.jpg", root, link)


def test_ingest_checks_hardlink_support_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(
        "photostow.oxygen.os.link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("nope")),
    )

    with pytest.raises(OSError, match="hardlinks are unavailable"):
        ingest(incoming, root / "photo.jpg", root)


def test_ingest_checks_resources_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(
        "photostow.oxygen.shutil.disk_usage",
        lambda path: __import__("shutil")._ntuple_diskusage(1, 1, 0),
    )

    with pytest.raises(OSError, match="no free bytes"):
        ingest(incoming, root / "photo.jpg", root)


def test_ingest_creates_object_and_visible_hardlink(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "2006" / "renamed.jpg"

    digest = ingest(incoming, destination, root)

    assert destination.exists()
    assert destination.stat().st_ino == object_path(root, digest).stat().st_ino
    assert not (root / ".photostow.lock").exists()
    assert verify_objects(root / ".objects") == []


def test_ingest_cli_requires_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "sys.argv", ["oxygen-ingest", str(tmp_path / "source.jpg"), "photo.jpg"]
    )
    with pytest.raises(SystemExit) as error:
        ingest_main()
    assert error.value.code == 2


def test_ingest_cli_rejects_destination_outside_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", str(source), str(outside), "--root", str(root)],
    )

    assert ingest_main() == 1
    assert not outside.exists()
    assert "failed" in capsys.readouterr().err


def test_ingest_cli_rejects_symlinked_destination_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (root / "album").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", str(source), "album/photo.jpg", "--root", str(root)],
    )

    assert ingest_main() == 1
    assert not (external / "photo.jpg").exists()


def test_ingest_cli_rejects_relative_destination_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    outside = tmp_path / "outside.jpg"
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", str(source), "../outside.jpg", "--root", str(root)],
    )

    assert ingest_main() == 1
    assert not outside.exists()


def test_ingest_cli_rejects_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"new")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    destination.write_bytes(b"existing")
    monkeypatch.setattr(
        "sys.argv", ["oxygen-ingest", str(source), "photo.jpg", "--root", str(root)]
    )

    assert ingest_main() == 1
    assert destination.read_bytes() == b"existing"


def test_ingest_cli_rejects_concurrent_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    monkeypatch.setattr(
        "sys.argv", ["oxygen-ingest", str(source), "photo.jpg", "--root", str(root)]
    )
    with _lock_path(root).open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        assert ingest_main() == 1
    assert not destination.exists()


def test_ingest_cli_accepts_same_digest_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"same")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    destination.write_bytes(b"same")
    monkeypatch.setattr(
        "sys.argv", ["oxygen-ingest", str(source), "photo.jpg", "--root", str(root)]
    )

    assert ingest_main() == 0
    assert destination.read_bytes() == b"same"
    assert destination.stat().st_nlink == 1


def test_ingest_cli_rejects_missing_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-ingest",
            str(tmp_path / "missing.jpg"),
            str(destination),
            "--root",
            str(root),
        ],
    )

    assert ingest_main() == 1
    assert not destination.exists()
    assert "oxygen-ingest failed:" in capsys.readouterr().err


def test_ingest_cli_reports_failure_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", "source.jpg", "photo.jpg", "--root", str(tmp_path)],
    )
    monkeypatch.setattr(
        "photostow.oxygen_ingest_cli.ingest",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("bad destination")),
    )

    assert ingest_main() == 1
    assert "oxygen-ingest failed: bad destination" in capsys.readouterr().err


def test_ingest_cli_reports_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"source")
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", str(source), str(root / "photo.jpg"), "--root", str(root)],
    )
    monkeypatch.setattr(
        "photostow.oxygen_ingest_cli.ingest",
        lambda *args, **kwargs: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert ingest_main() == 130
    assert "oxygen-ingest interrupted; publish incomplete" in capsys.readouterr().err


def test_ingest_cli_rejects_symlink_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "incoming.jpg"
    source.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    real = tmp_path / "real-objects"
    real.mkdir()
    object_root = tmp_path / "objects"
    object_root.symlink_to(real, target_is_directory=True)
    destination = root / "photo.jpg"
    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-ingest",
            str(source),
            str(destination),
            "--root",
            str(root),
            "--object-root",
            str(object_root),
        ],
    )

    assert ingest_main() == 1
    assert not destination.exists()


def test_ingest_cli_passes_custom_object_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "incoming.jpg"
    source.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    object_root = tmp_path / "objects"
    calls = []

    def fake_ingest(
        source: Path, destination: Path, root: Path, object_root: Path
    ) -> str:
        calls.append((source, destination, root, object_root))
        return "a" * 64

    monkeypatch.setattr(
        "sys.argv",
        [
            "oxygen-ingest",
            str(source),
            "photo.jpg",
            "--root",
            str(root),
            "--object-root",
            str(object_root),
        ],
    )
    monkeypatch.setattr("photostow.oxygen_ingest_cli.ingest", fake_ingest)

    assert ingest_main() == 0
    assert calls == [(source, root / "photo.jpg", root, object_root)]


def test_ingest_cli_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    monkeypatch.setattr(
        "sys.argv",
        ["oxygen-ingest", str(incoming), "photo.jpg", "--root", str(root)],
    )

    assert ingest_main() == 0
    output = capsys.readouterr().out
    assert "ingested" in output
    assert f"sha256={hashlib.sha256(b'incoming').hexdigest()}" in output
    assert f"source={incoming.resolve()}" in output
    assert f"object-root={(root / '.objects').resolve()}" in output
    assert "elapsed=" in output
    assert f"destination={(root / 'photo.jpg').resolve()}" in output
    assert (root / "photo.jpg").is_file()


def test_ingest_does_not_overwrite_racing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    original_link = __import__("os").link

    def race(source: Path, target: Path, **kwargs: object) -> None:
        if target == destination:
            destination.write_bytes(b"racer")
            raise FileExistsError(target)
        original_link(source, target, **kwargs)

    monkeypatch.setattr("photostow.oxygen.os.link", race)
    with pytest.raises(FileExistsError):
        ingest(incoming, destination, root)
    assert destination.read_bytes() == b"racer"


def test_ingest_rejects_concurrent_operation(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    with _lock_path(root).open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(OSError, match="another Oxygen operation"):
            ingest(incoming, root / "photo.jpg", root)


def test_ingest_rejects_existing_object_with_visible_reference(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    source.write_bytes(b"photo")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    object_root = root / ".objects"
    digest = hashlib.sha256(b"photo").hexdigest()
    target = object_path(root, digest, object_root)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"photo")
    visible = root / "existing.jpg"
    os.link(target, visible)

    with pytest.raises(ValueError, match="visible reference"):
        ingest(source, destination, root, object_root)
    assert not destination.exists()


def test_ingest_same_digest_destination_is_noop(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    destination.write_bytes(b"incoming")

    digest = ingest(incoming, destination, root)

    assert destination.read_bytes() == b"incoming"
    assert destination.stat().st_nlink == 1
    assert len(digest) == 64


def test_ingest_refuses_existing_destination(tmp_path: Path) -> None:
    incoming = tmp_path / "incoming.jpg"
    incoming.write_bytes(b"incoming")
    root = tmp_path / "archive"
    root.mkdir()
    destination = root / "photo.jpg"
    destination.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        ingest(incoming, destination, root)


def test_migrate_publishes_before_discovering_next_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = tmp_path / "a.jpg"
    second = tmp_path / "b.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    original_digest = oxygen._stable_digest
    calls = 0

    def digest(path: Path):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert os.path.samefile(
                first, object_path(tmp_path, hashlib.sha256(b"a").hexdigest())
            )
        return original_digest(path)

    monkeypatch.setattr(oxygen, "_stable_digest", digest)
    assert migrate(tmp_path, dry_run=False) == 2


def test_migrate_records_link_count_failure(tmp_path: Path) -> None:
    source = tmp_path / "photo.jpg"
    source.write_bytes(b"photo")
    os.link(source, tmp_path / "second-visible.jpg")
    failures = tmp_path / "migration-failures.jsonl"

    with pytest.raises(OSError, match="unexpected link count"):
        migrate(tmp_path, dry_run=False, failure_list=failures)

    record = __import__("json").loads(failures.read_text(encoding="utf-8"))
    assert record["path"] == str(source)
    assert record["digest"] == hashlib.sha256(b"photo").hexdigest()
    assert record["object_path"].endswith(record["digest"][2:])
    assert not (tmp_path / ".objects").exists()


def test_migrate_continues_after_recorded_failure(tmp_path: Path) -> None:
    bad = tmp_path / "a-bad.jpg"
    bad.write_bytes(b"bad")
    os.link(bad, tmp_path / "a-second-visible.jpg")
    good = tmp_path / "b-good.jpg"
    good.write_bytes(b"good")
    failures = tmp_path / "migration-failures.jsonl"

    with pytest.raises(OSError, match="migration failed"):
        migrate(
            tmp_path,
            dry_run=False,
            failure_list=failures,
            continue_on_error=True,
        )

    digest = hashlib.sha256(b"good").hexdigest()
    assert os.path.samefile(good, object_path(tmp_path, digest))
    assert len(failures.read_text(encoding="utf-8").splitlines()) == 2


def test_migrate_checks_hardlink_support_before_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "photostow.oxygen.os.link",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("nope")),
    )

    with pytest.raises(OSError, match="hardlinks are unavailable"):
        migrate(tmp_path, dry_run=False)


def test_migrate_checks_free_space_before_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"photo")
    monkeypatch.setattr(
        "photostow.oxygen.shutil.disk_usage",
        lambda path: __import__("shutil")._ntuple_diskusage(1, 1, 0),
    )

    with pytest.raises(OSError, match="no free bytes"):
        migrate(tmp_path, dry_run=False)


def test_migrate_rejects_repeated_source_changes_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_digest = oxygen._stable_digest
    calls: dict[Path, int] = {}

    def race(path: Path) -> tuple[str, os.stat_result]:
        calls[path] = calls.get(path, 0) + 1
        if calls[path] == 2:
            path.write_bytes(b"changed")
        return original_digest(path)

    monkeypatch.setattr("photostow.oxygen._stable_digest", race)

    for attempt in range(3):
        root = tmp_path / str(attempt)
        root.mkdir()
        source = root / "photo.jpg"
        source.write_bytes(b"original")
        with pytest.raises(OSError, match="file changed while publishing"):
            migrate(root, dry_run=False)
        assert source.read_bytes() == b"changed"


def test_migrate_handles_object_creation_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    original_link = __import__("os").link

    def link_then_race(source: Path, target: Path, **kwargs: object) -> None:
        original_link(source, target, **kwargs)
        if source.stat().st_size != 0:
            raise FileExistsError(target)

    monkeypatch.setattr("photostow.oxygen.os.link", link_then_race)
    assert migrate(tmp_path, dry_run=False) == 1


def test_migrate_validates_root_before_locking(tmp_path: Path) -> None:
    with pytest.raises(NotADirectoryError):
        migrate(tmp_path / "missing")


def test_migrate_tightens_existing_lock_permissions(tmp_path: Path) -> None:
    lock_path = _lock_path(tmp_path)
    lock_path.unlink(missing_ok=True)
    lock_path.touch(mode=0o644)

    migrate(tmp_path)

    assert lock_path.stat().st_mode & 0o777 == 0o600
    lock_path.unlink()


def test_migrate_rejects_symlinked_operation_lock(tmp_path: Path) -> None:
    lock_path = _lock_path(tmp_path)
    lock_path.unlink(missing_ok=True)
    target = tmp_path / "lock-target"
    target.write_text("", encoding="utf-8")
    lock_path.symlink_to(target)
    try:
        with pytest.raises(OSError):
            migrate(tmp_path)
    finally:
        lock_path.unlink(missing_ok=True)


def test_migrate_rejects_concurrent_operation(tmp_path: Path) -> None:
    lock_path = _lock_path(tmp_path)
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with pytest.raises(OSError, match="another Oxygen operation"):
            migrate(tmp_path)


def test_migrate_rejects_symlink_existing_object(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    digest = __import__("hashlib").sha256(b"photo").hexdigest()
    external = tmp_path / "external"
    external.write_bytes(b"photo")
    object_file = object_path(tmp_path, digest)
    object_file.parent.mkdir(parents=True)
    object_file.symlink_to(external)

    with pytest.raises(OSError, match="object path is a symlink"):
        migrate(tmp_path)


def test_migrate_rejects_corrupt_existing_object(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    digest = __import__("hashlib").sha256(b"photo").hexdigest()
    object_file = tmp_path / ".objects" / "sha256" / digest[:2] / digest[2:]
    object_file.parent.mkdir(parents=True)
    object_file.write_bytes(b"corrupt")

    with pytest.raises(OSError, match="does not match digest"):
        migrate(tmp_path)


def test_migrate_rejects_symlink_object_root(tmp_path: Path) -> None:
    target = tmp_path / "real-objects"
    target.mkdir()
    link = tmp_path / "objects"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="must not be a symlink"):
        migrate(tmp_path, object_root=link)


def test_process_record_rejects_symlink_destination_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    path_parent = tmp_path / "linked"
    path_parent.symlink_to(target, target_is_directory=True)
    path = path_parent / "photo.jpg"
    digest = hashlib.sha256(b"photo").hexdigest()

    with pytest.raises(OSError, match="migration path parent is a symlink"):
        oxygen._process_record(
            tmp_path, tmp_path / "objects", digest, path, False, False
        )


def test_process_record_rejects_symlink_destination(tmp_path: Path) -> None:
    target = tmp_path / "target.jpg"
    target.write_bytes(b"photo")
    path = tmp_path / "photo.jpg"
    path.symlink_to(target)
    digest = hashlib.sha256(b"photo").hexdigest()

    with pytest.raises(OSError, match="migration path is a symlink"):
        oxygen._process_record(
            tmp_path, tmp_path / "objects", digest, path, False, False
        )


def test_migrate_rejects_boolean_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        migrate(tmp_path, limit=True)
    assert str(error.value) == "limit must be a nonnegative integer"


def test_migrate_rejects_negative_limit(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        migrate(tmp_path, limit=-1)
    assert str(error.value) == "limit must not be negative"


def test_migrate_rejects_symlink_object_shard(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    target = tmp_path / "real-shard"
    target.mkdir()
    object_root = tmp_path / "objects"
    digest = __import__("hashlib").sha256(b"photo").hexdigest()
    shard = object_root / "sha256" / digest[:2]
    shard.parent.mkdir(parents=True)
    shard.symlink_to(target, target_is_directory=True)

    with pytest.raises(OSError, match="object path parent is a symlink"):
        migrate(tmp_path, dry_run=False, object_root=object_root)


def test_migrate_rejects_symlink_object_store(tmp_path: Path) -> None:
    target = tmp_path / "real-store"
    target.mkdir()
    object_root = tmp_path / "objects"
    object_root.mkdir()
    (object_root / "sha256").symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="object store must not be a symlink"):
        migrate(tmp_path, object_root=object_root)


def test_migrate_rejects_symlink_object_root_parent(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    parent = tmp_path / "linked"
    parent.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="object root parent must not be a symlink"):
        migrate(tmp_path, object_root=parent / "objects")


def test_migrate_rejects_file_object_root(tmp_path: Path) -> None:
    object_root = tmp_path / "objects"
    object_root.write_bytes(b"not a directory")

    with pytest.raises(NotADirectoryError):
        migrate(tmp_path, object_root=object_root)


def test_migrate_uses_custom_object_root(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")
    objects = tmp_path / "objects"

    assert migrate(tmp_path, dry_run=False, object_root=objects) == 1
    assert object_path(tmp_path, "a" * 64, objects) == objects / "sha256" / "aa" / (
        "a" * 62
    )
    assert objects.exists()


def test_migrate_rejects_different_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "photostow.oxygen._device", lambda path: 1 if path == tmp_path else 2
    )
    with pytest.raises(ValueError, match="same filesystem"):
        migrate(tmp_path, object_root=tmp_path / "objects")


def test_visible_files_empty_tree_ignores_exclude_patterns(tmp_path: Path) -> None:
    assert visible_files(tmp_path, exclude=("*.jpg", "skip", "[a-z]*")) == []


def test_visible_files_excludes_custom_object_root(tmp_path: Path) -> None:
    objects = tmp_path / "objects"
    objects.mkdir()
    (objects / "object").write_bytes(b"object")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, excluded_root=objects) == [photo]


def test_visible_files_missing_excluded_root_is_canonical(tmp_path: Path) -> None:
    excluded = tmp_path / "objects"
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, excluded_root=excluded) == [photo]
    excluded.mkdir()
    (excluded / "object").write_bytes(b"object")
    assert visible_files(tmp_path, excluded_root=excluded) == [photo]


def test_visible_files_excluded_root_normalizes_parent_alias(tmp_path: Path) -> None:
    (tmp_path / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, excluded_root=tmp_path / "objects" / "..") == []


def test_visible_files_excluded_root_normalizes_trailing_separator(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    (excluded / "object").write_bytes(b"object")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, excluded_root=Path(str(excluded) + "/")) == [photo]


def test_visible_files_selection_normalizes_parent_alias(tmp_path: Path) -> None:
    album = tmp_path / "album"
    album.mkdir()
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, selected=album / "..") == [photo]


def test_visible_files_selection_normalizes_trailing_separator(tmp_path: Path) -> None:
    selected = tmp_path / "album"
    selected.mkdir()
    photo = selected / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, selected=Path(str(selected) + "/")) == [photo]


def test_visible_files_selection_alias_preserves_excluded_root(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    (excluded / "object").write_bytes(b"object")
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(
        tmp_path, selected=excluded / "..", excluded_root=excluded
    ) == [photo]


def test_visible_files_excluded_root_precedes_patterns(tmp_path: Path) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    (excluded / "photo.jpg").write_bytes(b"photo")

    assert (
        visible_files(
            tmp_path, selected=excluded, excluded_root=excluded, exclude=("*.tmp",)
        )
        == []
    )


def test_visible_files_supports_glob_exclusions(tmp_path: Path) -> None:
    (tmp_path / "keep.jpg").write_bytes(b"keep")
    (tmp_path / "skip.tmp").write_bytes(b"skip")
    hidden = tmp_path / "skip-dir"
    hidden.mkdir()
    (hidden / "photo.jpg").write_bytes(b"photo")

    assert visible_files(tmp_path, exclude=("*.tmp", "skip-dir")) == [
        tmp_path / "keep.jpg"
    ]


def test_visible_files_excludes_selected_file_by_glob(tmp_path: Path) -> None:
    photo = tmp_path / "skip.tmp"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, photo, exclude=("*.tmp",)) == []


def test_visible_files_selected_file_normalizes_combined_aliases(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    alias = str(tmp_path / "album" / ".." / "." / "photo.jpg")

    assert visible_files(tmp_path, selected=Path(alias.replace("/", "///"))) == [
        selected
    ]


def test_visible_files_selected_file_normalizes_redundant_separators(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(
        tmp_path, selected=Path(str(tmp_path) + "///photo.jpg///")
    ) == [selected]


def test_visible_files_selected_file_normalizes_parent_alias_and_separator(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(
        tmp_path, selected=Path(str(tmp_path / "album" / ".." / "photo.jpg") + "/")
    ) == [selected]


def test_visible_files_selected_file_normalizes_dot_alias_and_separator(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(
        tmp_path, selected=Path(str(tmp_path / "." / "photo.jpg") + "/")
    ) == [selected]


def test_visible_files_selected_file_normalizes_dot_alias(tmp_path: Path) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(tmp_path, selected=tmp_path / "." / "photo.jpg") == [selected]


def test_visible_files_selected_file_normalizes_excluded_root_separator(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    selected = excluded / "object"
    selected.write_bytes(b"object")

    assert (
        visible_files(
            tmp_path, selected=selected, excluded_root=Path(str(excluded) + "/")
        )
        == []
    )


def test_visible_files_selected_file_alias_respects_excluded_root(
    tmp_path: Path,
) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    selected = excluded / "object"
    selected.write_bytes(b"object")

    assert (
        visible_files(
            tmp_path,
            selected=excluded / ".." / "objects" / "object",
            excluded_root=excluded,
        )
        == []
    )


def test_visible_files_selected_file_alias_normalizes_external_excluded_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path.parent / f"{tmp_path.name}-external"
    external.mkdir()

    assert visible_files(
        tmp_path,
        selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
        excluded_root=Path(str(external / ".." / external.name) + "///"),
    ) == [selected]


def test_visible_files_selected_file_rejects_file_valued_external_parent(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    parent = tmp_path / "external-file-parent"
    parent.write_bytes(b"not a directory")

    with pytest.raises(NotADirectoryError):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root=parent / "objects",
        )


def test_visible_files_selected_file_propagates_denied_external_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    denied = tmp_path / "denied-parent"
    external = denied / "objects"
    original = Path.exists

    def failing_exists(path: Path) -> bool:
        if path == denied:
            raise PermissionError("external parent denied")
        return original(path)

    monkeypatch.setattr(Path, "exists", failing_exists)
    with pytest.raises(PermissionError, match="external parent denied"):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_selected_file_rejects_external_root_file(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external-file"
    external.write_bytes(b"not a directory")

    with pytest.raises(NotADirectoryError):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root=external,
        )


def test_visible_files_selected_file_rejects_invalid_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    with pytest.raises(ValueError, match="excluded root must be a Path"):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root="external-objects",  # type: ignore[arg-type]
        )


def test_visible_files_propagates_root_resolve_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.resolve

    def failing_resolve(path: Path, *args, **kwargs) -> Path:
        if path == tmp_path:
            raise PermissionError("root resolve denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    with pytest.raises(PermissionError, match="root resolve denied"):
        visible_files(tmp_path)


def test_visible_files_propagates_root_is_dir_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.is_dir

    def failing_is_dir(path: Path) -> bool:
        if path == tmp_path:
            raise PermissionError("root directory check denied")
        return original(path)

    monkeypatch.setattr(Path, "is_dir", failing_is_dir)
    with pytest.raises(PermissionError, match="root directory check denied"):
        visible_files(tmp_path)


def test_visible_files_propagates_root_directory_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.is_dir

    def failing_is_dir(path: Path) -> bool:
        if path == tmp_path:
            raise PermissionError("root directory denied")
        return original(path)

    monkeypatch.setattr(Path, "is_dir", failing_is_dir)
    with pytest.raises(PermissionError, match="root directory denied"):
        visible_files(tmp_path)


def test_visible_files_propagates_root_symlink_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == tmp_path:
            raise PermissionError("root link denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(PermissionError, match="root link denied"):
        visible_files(tmp_path)


def test_visible_files_checks_selected_containment_before_exclude_pattern(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    with pytest.raises(ValueError, match="selection must be beneath root"):
        visible_files(root, selected=selected, exclude=("*.jpg",))


def test_visible_files_checks_selected_containment_before_reserved_name(
    tmp_path: Path,
) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    selected = tmp_path / ".DS_Store"
    selected.write_bytes(b"metadata")

    with pytest.raises(ValueError, match="selection must be beneath root"):
        visible_files(root, selected=selected)


def test_visible_files_propagates_selected_metadata_link_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == selected:
            raise PermissionError("selected metadata check denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(PermissionError, match="selected metadata check denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_propagates_selected_is_file_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    original = Path.is_file

    def failing_is_file(path: Path) -> bool:
        if path == selected:
            raise PermissionError("selection file check denied")
        return original(path)

    monkeypatch.setattr(Path, "is_file", failing_is_file)
    with pytest.raises(PermissionError, match="selection file check denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_selected_file_propagates_external_root_resolve_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external"
    external.mkdir()
    original = Path.resolve

    def failing_resolve(path: Path, *args, **kwargs) -> Path:
        if path == external:
            raise PermissionError("external resolve denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    with pytest.raises(PermissionError, match="external resolve denied"):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_selected_file_propagates_external_root_directory_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external"
    external.mkdir()
    original = Path.is_dir

    def failing_is_dir(path: Path) -> bool:
        if path == external:
            raise PermissionError("external directory denied")
        return original(path)

    monkeypatch.setattr(Path, "is_dir", failing_is_dir)
    with pytest.raises(PermissionError, match="external directory denied"):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_selected_file_propagates_external_root_link_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external"
    external.mkdir()
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == external:
            raise PermissionError("external link denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(PermissionError, match="external link denied"):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_propagates_selected_parent_link_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "selected-parent"
    selected = parent / "photo.jpg"
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == parent:
            raise PermissionError("selection parent link denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(PermissionError, match="selection parent link denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_propagates_selected_exists_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    original = Path.exists

    def failing_exists(path: Path) -> bool:
        if path == selected:
            raise PermissionError("selection access denied")
        return original(path)

    monkeypatch.setattr(Path, "exists", failing_exists)
    with pytest.raises(PermissionError, match="selection access denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_propagates_selected_resolve_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    original = Path.resolve

    def failing_resolve(path: Path, *args, **kwargs) -> Path:
        if path == selected:
            raise PermissionError("selection resolve denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", failing_resolve)
    with pytest.raises(PermissionError, match="selection resolve denied"):
        visible_files(tmp_path, selected=selected)


def test_visible_files_selected_file_propagates_external_root_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external"
    external.mkdir()
    original = Path.is_symlink

    def failing_is_symlink(path: Path) -> bool:
        if path == external:
            raise PermissionError("excluded root denied")
        return original(path)

    monkeypatch.setattr(Path, "is_symlink", failing_is_symlink)
    with pytest.raises(PermissionError, match="excluded root denied"):
        visible_files(tmp_path, selected=selected, excluded_root=external)


def test_visible_files_selected_file_rejects_external_root_symlink_parent(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    target = tmp_path / "external-target-parent"
    target.mkdir()
    link = tmp_path / "external-link-parent"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root has symlinked parent"):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root=link / "objects",
        )


def test_visible_files_selected_file_rejects_dangling_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external-link"
    external.symlink_to(tmp_path / "missing-external", target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root is a symlink"):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root=external,
        )


def test_visible_files_selected_file_rejects_symlink_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    target = tmp_path / "external-target"
    target.mkdir()
    external = tmp_path / "external-link"
    external.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="excluded root is a symlink"):
        visible_files(
            tmp_path,
            selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
            excluded_root=external,
        )


def test_visible_files_selected_file_alias_normalizes_combined_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path.parent / f"{tmp_path.name}-external-combined"
    external.mkdir()

    assert visible_files(
        tmp_path,
        selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
        excluded_root=Path(str(external / ".." / "." / external.name) + "///"),
    ) == [selected]


def test_visible_files_selected_file_alias_normalizes_dot_external_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path.parent / f"{tmp_path.name}-external-dot"
    external.mkdir()

    assert visible_files(
        tmp_path,
        selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
        excluded_root=Path(str(external / ".") + "///"),
    ) == [selected]


def test_visible_files_selected_file_alias_allows_missing_external_excluded_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(
        tmp_path,
        selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
        excluded_root=tmp_path / "missing-objects" / "." / ".." / "missing-objects",
    ) == [selected]


def test_visible_files_selected_file_alias_allows_external_excluded_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    external = tmp_path / "external-objects"
    external.mkdir()

    assert visible_files(
        tmp_path,
        selected=Path(str(tmp_path / "." / "photo.jpg") + "///"),
        excluded_root=external,
    ) == [selected]


def test_visible_files_selected_file_allows_missing_external_excluded_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")

    assert visible_files(
        tmp_path, selected=selected, excluded_root=tmp_path / "missing-objects"
    ) == [selected]


def test_visible_files_selected_file_normalizes_relative_external_excluded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    selected = root / "photo.jpg"
    selected.write_bytes(b"photo")
    (tmp_path / "external-objects").mkdir()
    monkeypatch.chdir(tmp_path)

    assert visible_files(
        Path("root"),
        selected=Path("root/photo.jpg"),
        excluded_root=Path("external-objects//../external-objects///"),
    ) == [selected]


def test_visible_files_selected_file_allows_relative_external_excluded_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    selected = root / "photo.jpg"
    selected.write_bytes(b"photo")
    (tmp_path / "external-objects").mkdir()
    monkeypatch.chdir(tmp_path)

    assert visible_files(
        Path("root"),
        selected=Path("root/photo.jpg"),
        excluded_root=Path("external-objects"),
    ) == [selected]


def test_visible_files_selected_file_allows_external_excluded_root(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "photo.jpg"
    selected.write_bytes(b"photo")
    excluded = tmp_path / "external-objects"
    excluded.mkdir()

    assert visible_files(tmp_path, selected=selected, excluded_root=excluded) == [
        selected
    ]


def test_visible_files_selected_file_respects_excluded_root(tmp_path: Path) -> None:
    excluded = tmp_path / "objects"
    excluded.mkdir()
    selected = excluded / "object"
    selected.write_bytes(b"object")

    assert visible_files(tmp_path, selected=selected, excluded_root=excluded) == []


@pytest.mark.parametrize("name", [".DS_Store", ".photostow.lock", "photos-oxygen-sha"])
def test_visible_files_selected_file_filters_reserved_names(
    tmp_path: Path, name: str
) -> None:
    selected = tmp_path / name
    selected.write_bytes(b"reserved")

    assert visible_files(tmp_path, selected=selected) == []


def test_visible_files_selects_single_file(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"photo")

    assert visible_files(tmp_path, photo) == [photo]


def test_visible_files_selects_subtree(tmp_path: Path) -> None:
    year = tmp_path / "2006"
    year.mkdir()
    (year / "photo.jpg").write_bytes(b"photo")
    (tmp_path / "other.jpg").write_bytes(b"other")

    assert visible_files(tmp_path, year) == [year / "photo.jpg"]
