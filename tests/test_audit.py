from pathlib import Path

from photostow.audit import (
    RemoteFile,
    duplicate_groups,
    human_bytes,
    parse_duplicate_group_file,
    sort_duplicate_group,
    unknown_files,
)


def test_unknown_files_compares_canonical_paths() -> None:
    remote = [RemoteFile("/var/services/photo/2023/a.jpg", 10)]
    ledger = ["abc  /var/services/photo/2023/a.jpg"]

    assert unknown_files(remote, ledger) == []


def test_unknown_files_reports_paths_absent_from_ledger() -> None:
    remote = [RemoteFile("/var/services/photo/2023/new.jpg", 10)]
    ledger = ["abc  /var/services/photo/2023/old.jpg"]

    assert unknown_files(remote, ledger) == remote


def test_duplicate_groups_by_hash() -> None:
    groups = duplicate_groups([
        "abc  /one.jpg",
        "def  /two.jpg",
        "abc  /three.jpg",
    ])

    assert groups == [["/one.jpg", "/three.jpg"]]


def test_duplicate_groups_can_filter_current_paths() -> None:
    groups = duplicate_groups(
        ["abc  /one.jpg", "abc  /gone.jpg", "abc  /two.jpg"],
        current_paths={"/one.jpg", "/two.jpg"},
    )

    assert groups == [["/one.jpg", "/two.jpg"]]


def test_sort_duplicate_group_prefers_pixette_then_year_file() -> None:
    assert sort_duplicate_group(
        [
            "/var/services/photo/new/a.jpg",
            "/var/services/photo/2023/a.jpg",
            "/var/services/photo/2023/a_pixette_removed.jpg",
        ]
    ) == [
        "/var/services/photo/2023/a_pixette_removed.jpg",
        "/var/services/photo/2023/a.jpg",
        "/var/services/photo/new/a.jpg",
    ]


def test_parse_duplicate_group_file(tmp_path: Path) -> None:
    path = tmp_path / "groups.txt"
    path.write_text("/a\n/b\n\n/c\n/d\n", encoding="utf-8")

    assert parse_duplicate_group_file(path) == [["/a", "/b"], ["/c", "/d"]]


def test_human_bytes() -> None:
    assert human_bytes(1024 * 1024) == "1.0 MiB"
