from photostow.audit import RemoteFile, duplicate_groups, human_bytes, unknown_files


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


def test_human_bytes() -> None:
    assert human_bytes(1024 * 1024) == "1.0 MiB"
