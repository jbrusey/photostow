from photostow.audit import (
    RemoteFile,
    duplicate_groups,
    human_bytes,
    path_aliases,
    unknown_files,
)


def test_path_aliases_treats_synology_roots_as_same() -> None:
    assert path_aliases("/volume1/photo/2023/a.jpg") == {
        "/volume1/photo/2023/a.jpg",
        "/var/services/photo/2023/a.jpg",
    }


def test_unknown_files_uses_path_aliases() -> None:
    remote = [RemoteFile("/volume1/photo/2023/a.jpg", 10)]
    ledger = ["abc  /var/services/photo/2023/a.jpg"]

    assert unknown_files(remote, ledger) == []


def test_duplicate_groups_by_hash() -> None:
    groups = duplicate_groups([
        "abc  /one.jpg",
        "def  /two.jpg",
        "abc  /three.jpg",
    ])

    assert groups == [["/one.jpg", "/three.jpg"]]


def test_human_bytes() -> None:
    assert human_bytes(1024 * 1024) == "1.0 MiB"
