from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from photostow import audit, remote
from photostow.audit import (
    RemoteFile,
    duplicate_groups,
    human_bytes,
    parse_duplicate_group_file,
    sort_duplicate_group,
    unknown_files,
)


def test_remote_files_rejects_non_utf8_shared_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: type("Result", (), {"stdout": b"\xff malformed"})(),
    )
    monkeypatch.setattr(
        audit, "remote_path_beneath", lambda *args: pytest.fail("parsed too soon")
    )

    with pytest.raises(ValueError, match="SSH output is not UTF-8"):
        audit.remote_files("oxygen", "/photo")


def test_remote_files_shell_quotes_root(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[str] = []

    def fake_ssh_stdout(host: str, command: str) -> str:
        commands.append(command)
        return "10 /var/services/photo;echo unsafe/a.jpg\n"

    monkeypatch.setattr(audit, "ssh_stdout", fake_ssh_stdout)

    audit.remote_files("oxygen", "/var/services/photo;echo unsafe")

    assert commands[0].startswith("find '/var/services/photo;echo unsafe/' ")
    assert "-path '*/@eaDir' -prune -o" in commands[0]
    assert "-path '*/.objects' -prune -o" in commands[0]
    assert "-name 'photos-oxygen-sha*' -prune -o" in commands[0]
    assert "stat -c '%s %n'" in commands[0]
    assert "-exec stat" in commands[0]
    assert "xargs" not in commands[0]


@pytest.mark.parametrize(
    "record",
    [
        "oops /var/services/photo/a.jpg",
        "-1 /var/services/photo/a.jpg",
        "malformed-record",
    ],
)
def test_remote_files_rejects_malformed_sizes(
    monkeypatch: pytest.MonkeyPatch, record: str
) -> None:
    monkeypatch.setattr(audit, "ssh_stdout", lambda host, command: record + "\0")

    with pytest.raises(ValueError, match="audit"):
        audit.remote_files("oxygen", "/var/services/photo")


@pytest.mark.parametrize("root", [".", "./"])
def test_remote_files_canonicalizes_current_directory_output(
    monkeypatch: pytest.MonkeyPatch, root: str
) -> None:
    monkeypatch.setattr(audit, "ssh_stdout", lambda host, command: "10 ./photo.jpg\0")

    assert audit.remote_files("oxygen", root) == [RemoteFile("photo.jpg", 10)]


def test_remote_files_normalizes_in_root_dot_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit, "ssh_stdout", lambda host, command: "10 /photo/./one.jpg\0"
    )

    assert audit.remote_files("oxygen", "/photo") == [RemoteFile("/photo/one.jpg", 10)]


def test_remote_files_deduplicates_same_size_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "ssh_stdout",
        lambda host, command: "10 /photo/one.jpg\0" + "10 /photo/one.jpg\0",
    )

    assert audit.remote_files("oxygen", "/photo") == [RemoteFile("/photo/one.jpg", 10)]


def test_remote_files_normalizes_in_root_dotdot_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "ssh_stdout",
        lambda host, command: (
            "10 /photo/album/../one.jpg" + chr(0) + "10 /photo/one.jpg" + chr(0)
        ),
    )

    assert audit.remote_files("oxygen", "/photo") == [RemoteFile("/photo/one.jpg", 10)]


def test_remote_files_rejects_conflicting_duplicate_sizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "ssh_stdout",
        lambda host, command: (
            "10 /photo/one.jpg" + chr(0) + "11 /photo/./one.jpg" + chr(0)
        ),
    )

    with pytest.raises(ValueError, match="conflicting audit sizes"):
        audit.remote_files("oxygen", "/photo")


def test_remote_files_rejects_dotdot_traversal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit, "ssh_stdout", lambda host, command: "10 /photo/../other.jpg\0"
    )

    with pytest.raises(ValueError, match="audit path escapes root"):
        audit.remote_files("oxygen", "/photo")


def test_remote_files_rejects_root_prefix_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit, "ssh_stdout", lambda host, command: "10 /photo2/file.jpg\0"
    )

    with pytest.raises(ValueError, match="audit path escapes root"):
        audit.remote_files("oxygen", "/photo")


def test_remote_files_rejects_outside_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        audit, "ssh_stdout", lambda host, command: "10 /other/photo.jpg\0"
    )

    with pytest.raises(ValueError, match="audit path escapes root"):
        audit.remote_files("oxygen", "/var/services/photo")


@pytest.mark.parametrize(
    "root, message",
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("\0", "must not contain NUL"),
    ],
)
def test_remote_files_rejects_invalid_root(root: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        audit.remote_files("oxygen", root)


def test_remote_files_handles_empty_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(audit, "ssh_stdout", lambda host, command: "")

    assert audit.remote_files("oxygen", "/var/services/photo") == []


def test_remote_files_preserves_newline_filenames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        audit,
        "ssh_stdout",
        lambda host, command: "10 /var/services/photo/a\nb.jpg\0",
    )

    assert audit.remote_files("oxygen", "/var/services/photo") == [
        RemoteFile("/var/services/photo/a\nb.jpg", 10)
    ]


def test_unknown_files_compares_canonical_paths() -> None:
    remote = [RemoteFile("/var/services/photo/2023/a.jpg", 10)]
    ledger = ["abc  /var/services/photo/2023/a.jpg"]

    assert unknown_files(remote, ledger) == []


def test_unknown_files_reports_paths_absent_from_ledger() -> None:
    remote = [RemoteFile("/var/services/photo/2023/new.jpg", 10)]
    ledger = ["abc  /var/services/photo/2023/old.jpg"]

    assert unknown_files(remote, ledger) == remote


def test_pixette_removed_variant_is_inserted_before_extension() -> None:
    assert (
        audit.pixette_removed_variant("/photo/a.jpeg")
        == "/photo/a_pixette_removed.jpeg"
    )
    assert audit.pixette_removed_variant("/photo/a") == "/photo/a_pixette_removed"
    assert audit.pixette_removed_variant("/photo/a_pixette_removed.jpeg") == (
        "/photo/a_pixette_removed.jpeg"
    )


def test_remote_duplicate_groups_includes_pixette_removed_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    digest = "a" * 64
    keep = "/var/services/photo/2019/a.jpeg"
    base = "/var/services/photo/to-import/a.jpeg"
    removed = "/var/services/photo/to-import/a_pixette_removed.jpeg"
    (tmp_path / "ledger").write_text(
        f"{digest}  {keep}\n{digest}  {base}\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        audit,
        "remote_files",
        lambda host, root: [RemoteFile(keep, 1), RemoteFile(removed, 1)],
    )

    assert audit.remote_duplicate_groups(
        "oxygen", "/var/services/photo", tmp_path / "ledger"
    ) == [[removed, keep]]


def test_duplicate_groups_by_hash() -> None:
    groups = duplicate_groups(
        [
            "abc  /one.jpg",
            "def  /two.jpg",
            "abc  /three.jpg",
        ]
    )

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


def test_write_duplicate_groups_rejects_whitespace_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["  ", "/c"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path must not be empty"


def test_write_duplicate_groups_rejects_cross_group_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/b"], ["/a", "/c"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path appears in multiple groups"


def test_write_duplicate_groups_rejects_repeated_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/a"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report group paths must be unique"


@pytest.mark.parametrize("group", [[], ["/a"]])
def test_write_duplicate_groups_rejects_short_group(
    tmp_path: Path, group: list[str]
) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([group], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report group must have two paths"


def test_write_duplicate_groups_rejects_empty_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["", "/c"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path must not be empty"


def test_write_duplicate_groups_rejects_missing_parent(tmp_path: Path) -> None:
    output = tmp_path / "missing" / "report.txt"
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/b"]], output)
    assert (
        str(error.value)
        == f"duplicate report parent is not a directory: {output.parent}"
    )
    assert not output.exists()


def test_write_duplicate_groups_rejects_directory_destination(tmp_path: Path) -> None:
    output = tmp_path / "out"
    output.mkdir()

    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/b"]], output)
    assert str(error.value) == f"duplicate report destination is not a file: {output}"
    assert list(output.iterdir()) == []


def test_write_duplicate_groups_rejects_nested_symlink_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    output_dir = tmp_path / "out"
    output_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups(
            [["/a", "/b"]], output_dir / "nested" / "report.txt"
        )
    assert str(error.value) == f"refusing symlink duplicate report parent: {output_dir}"
    assert list(target.iterdir()) == []


def test_write_duplicate_groups_rejects_symlink_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    output_dir = tmp_path / "out"
    output_dir.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/b"]], output_dir / "report.txt")
    assert str(error.value) == f"refusing symlink duplicate report parent: {output_dir}"
    assert list(target.iterdir()) == []


def test_write_duplicate_groups_rejects_symlink_output(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("keep", encoding="utf-8")
    output = tmp_path / "out.txt"
    output.symlink_to(target)

    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a", "/b"]], output)
    assert str(error.value) == f"refusing symlink duplicate report: {output}"
    assert target.read_text(encoding="utf-8") == "keep"


def test_write_duplicate_groups_validates_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "out.txt"
    with pytest.raises(ValueError):
        audit.write_duplicate_groups([["/valid", "/bad\npath"]], path)
    assert not path.exists()


def test_write_duplicate_groups_rejects_nul_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a\x00", "/b"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path must not contain NUL"


@pytest.mark.parametrize("path", ["/a\nb", "/a\rb"])
def test_write_duplicate_groups_rejects_line_terminator(
    tmp_path: Path, path: str
) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([[path, "/c"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path contains unsupported newline"


def test_write_duplicate_groups_rejects_newline_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        audit.write_duplicate_groups([["/a\nb", "/c"]], tmp_path / "out.txt")
    assert str(error.value) == "duplicate report path contains unsupported newline"


def test_write_duplicate_groups_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    groups = [
        [
            " /a photo-é.jpg\u2028\u2029\u0085\u001c\u001d\u001e\u000b\u000c",
            "/b\tphoto.jpg ",
        ],
        ["/c", "/d"],
    ]
    audit.write_duplicate_groups(groups, path)

    assert parse_duplicate_group_file(path) == groups


def test_write_duplicate_groups_empty_input(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    audit.write_duplicate_groups([], path)

    assert path.read_text(encoding="utf-8") == ""


def test_parse_duplicate_group_file_rejects_cross_group_path(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\n/b\n\n/a\n/c\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report path appears in multiple groups"


def test_parse_duplicate_group_file_rejects_repeated_path(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\n/a\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report group paths must be unique"


def test_parse_duplicate_group_file_rejects_directory_input(tmp_path: Path) -> None:
    path = tmp_path / "duplicates"
    path.mkdir()
    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == f"duplicate report input is not a file: {path}"


def test_parse_duplicate_group_file_rejects_missing_input(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == f"duplicate report input is not a file: {path}"


def test_parse_duplicate_group_file_rejects_symlink_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "duplicates.txt").write_text("/a\n/b\n", encoding="utf-8")
    input_dir = tmp_path / "input"
    input_dir.symlink_to(target, target_is_directory=True)
    path = input_dir / "duplicates.txt"

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert (
        str(error.value)
        == f"refusing symlink duplicate report input parent: {input_dir}"
    )


def test_parse_duplicate_group_file_rejects_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("/a\n/b\n", encoding="utf-8")
    path = tmp_path / "duplicates.txt"
    path.symlink_to(target)

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == f"refusing symlink duplicate report input: {path}"
    assert target.read_text(encoding="utf-8") == "/a\n/b\n"


def test_parse_duplicate_group_file_rejects_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_bytes(b"/a\n/b\xff\n")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report is not valid UTF-8"


def test_parse_duplicate_group_file_rejects_carriage_return(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\r/b\n/c\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report path contains unsupported newline"


def test_parse_duplicate_group_file_rejects_nul_path(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\n/b\x00\n/c\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report path must not contain NUL"


def test_parse_duplicate_group_file_rejects_empty_path(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\n  \n/c\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report path must not be empty"


def test_parse_duplicate_group_file_rejects_singleton(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text("/a\n", encoding="utf-8")

    with pytest.raises(ValueError) as error:
        parse_duplicate_group_file(path)
    assert str(error.value) == "duplicate report group must have two paths"


def test_parse_duplicate_group_file_ignores_empty_report(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.txt"
    path.write_text(" \n\n", encoding="utf-8")

    assert parse_duplicate_group_file(path) == []


def test_parse_duplicate_group_file(tmp_path: Path) -> None:
    path = tmp_path / "groups.txt"
    path.write_text("/a\n/b\n\n/c\n/d\n", encoding="utf-8")

    assert parse_duplicate_group_file(path) == [["/a", "/b"], ["/c", "/d"]]


@pytest.mark.parametrize("helper", [audit.human_bytes, audit.time_range])
def test_audit_size_helpers_reject_negative(
    helper: Callable[[int], str],
) -> None:
    with pytest.raises(ValueError) as error:
        helper(-1)
    assert str(error.value) == "audit byte total must not be negative"


def test_human_bytes_pib_boundary() -> None:
    assert audit.human_bytes(1024**5) == "1.0 PiB"


def test_human_bytes_tib_boundary() -> None:
    assert audit.human_bytes(1024**4) == "1.0 TiB"


def test_human_bytes_gib_boundary() -> None:
    assert audit.human_bytes(1024**3) == "1.0 GiB"


def test_human_bytes_mib_boundary() -> None:
    assert audit.human_bytes(1024**2) == "1.0 MiB"


def test_human_bytes_kib_boundary() -> None:
    assert audit.human_bytes(1024) == "1.0 KiB"


def test_human_bytes_before_kib_boundary() -> None:
    assert audit.human_bytes(1023) == "1023.0 B"


def test_human_bytes_zero() -> None:
    assert audit.human_bytes(0) == "0.0 B"


def test_time_range_large_bytes() -> None:
    assert audit.time_range(1024**3) == "0.2-0.9 minutes at 20-80 MiB/s"


def test_time_range_representative_bytes() -> None:
    assert audit.time_range(80 * 1024**2) == "0.0-0.1 minutes at 20-80 MiB/s"


def test_time_range_zero_bytes() -> None:
    assert audit.time_range(0) == "0.0-0.0 minutes at 20-80 MiB/s"


def test_human_bytes() -> None:
    assert human_bytes(1024 * 1024) == "1.0 MiB"
