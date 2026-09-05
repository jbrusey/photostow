from pathlib import Path

from photostow.paths import archive_path_aliases, canonical_archive_path


def test_archive_path_aliases_accept_resolved_archive_spelling(
    monkeypatch, tmp_path: Path
) -> None:
    physical = tmp_path / "photo"
    physical.mkdir()
    monkeypatch.setattr("photostow.paths.ARCHIVE_ROOT", tmp_path / "photo-link")
    (tmp_path / "photo-link").symlink_to(physical, target_is_directory=True)

    path = tmp_path / "photo-link" / "2024" / "a.jpg"
    assert canonical_archive_path(path) == str(path)
    assert str(physical / "2024" / "a.jpg") in archive_path_aliases(path)
