from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from photostow import cli, remote


def test_archive_review_make_target_preserves_safe_order() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    recipe = makefile.split("archive-reviewed:\n", 1)[1].split("\n\n", 1)[0]

    assert "copy-reviewed-to-oxygen" not in makefile
    assert recipe.index("copy-tree") < recipe.index("update-oxygen-ledger")
    assert recipe.index("update-oxygen-ledger") < recipe.index("install-oxygen-ledger")


def test_remote_sha256_skips_empty_input(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    assert remote.remote_sha256("oxygen", []) == ""


def test_remote_sha256_uses_nul_path_input(monkeypatch: Any) -> None:
    calls: list[dict[str, Any]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(
            stdout=b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  /var/services/photo/a.jpg\0"
            b"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  /var/services/photo/c.jpg\0"
        )

    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    assert (
        remote.remote_sha256(
            "oxygen", ["/var/services/photo/a.jpg", "/var/services/photo/c.jpg"]
        )
        == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  /var/services/photo/a.jpg\n"
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb  /var/services/photo/c.jpg\n"
    )
    assert calls[0]["input"] == (
        b"/var/services/photo/a.jpg\0/var/services/photo/c.jpg\0"
    )
    assert calls[0]["command"][-1] == "xargs -0 sha256sum --zero"


@pytest.mark.parametrize(
    "output",
    [
        b"not-a-digest  /var/services/photo/a.jpg\0",
        b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa  \0",
    ],
)
def test_remote_sha256_rejects_malformed_output(
    monkeypatch: Any, output: bytes
) -> None:
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=output)
    )

    with pytest.raises(ValueError) as error:
        remote.remote_sha256("oxygen", ["/var/services/photo/a.jpg"])
    assert str(error.value) == "remote hash output is malformed"


def test_remote_sha256_rejects_malformed_utf8(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"\xff"),
    )

    with pytest.raises(ValueError) as error:
        remote.remote_sha256("oxygen", ["/var/services/photo/a.jpg"])
    assert str(error.value) == "remote hash output is not UTF-8"


def test_remote_sha256_rejects_newline_output_paths(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=b"hash  /var/services/photo/a\nb.jpg\0"
        ),
    )

    with pytest.raises(ValueError, match="unsupported newline path"):
        remote.remote_sha256("oxygen", ["/var/services/photo/a\nb.jpg"])


def test_remote_path_beneath_normalizes_repeated_root_slashes() -> None:
    assert remote.remote_path_beneath("/photo///", "/photo/file.jpg")
    assert remote.remote_path_beneath("//", "/photo/file.jpg")
    assert remote.remote_path_beneath("/photo", "//photo/file.jpg")
    assert not remote.remote_path_beneath("/photo", "//photo2/file.jpg")


def test_remote_path_beneath_rejects_empty_or_whitespace_roots() -> None:
    assert not remote.remote_path_beneath("", "photo.jpg")
    assert not remote.remote_path_beneath("   ", "photo.jpg")


def test_remote_path_beneath_rejects_nul_values() -> None:
    assert not remote.remote_path_beneath("/photo\0", "/photo/file.jpg")
    assert not remote.remote_path_beneath("/photo", "/photo/file\0.jpg")


def test_remote_path_beneath_requires_strict_descendant() -> None:
    assert not remote.remote_path_beneath("/photo", "/photo")
    assert remote.remote_path_beneath("/photo", "/photo/file.jpg")


def test_remote_path_beneath_handles_current_directory_root() -> None:
    assert remote.remote_path_beneath(".", "photo.jpg")
    assert remote.remote_path_beneath(".", "./photo.jpg")
    assert not remote.remote_path_beneath(".", "../photo.jpg")


def test_remote_path_beneath_handles_empty_and_relative_roots() -> None:
    assert remote.remote_path_beneath("photo", "photo/album/one.jpg")
    assert remote.remote_path_beneath("photo/", "photo/file.jpg")
    assert remote.remote_path_beneath("photo/.", "photo/file.jpg")
    assert not remote.remote_path_beneath("photo/", "photo2/file.jpg")
    assert not remote.remote_path_beneath("photo", "/photo/album/one.jpg")
    assert not remote.remote_path_beneath("photo", "photo/../photo2/one.jpg")
    assert not remote.remote_path_beneath("", "anything/one.jpg")


def test_remote_path_beneath_handles_filesystem_root() -> None:
    assert remote.remote_path_beneath("/", "/photo/file.jpg")
    assert not remote.remote_path_beneath("/", "photo/file.jpg")


def test_remote_path_beneath_normalizes_posix_paths() -> None:
    assert remote.remote_path_beneath("/photo/", "/photo//album/one.jpg")
    assert not remote.remote_path_beneath("/photo/", "/photo2/one.jpg")
    assert not remote.remote_path_beneath("/photo/", "/photo/../other.jpg")


def test_remote_scanner_exclusions_are_centralized() -> None:
    assert remote.REMOTE_EXCLUDES == (
        "-path '*/@eaDir' -prune -o "
        "-path '*/.objects' -prune -o "
        "-path '*/._DAV' -prune -o "
        "-name '.afpDeleted*' -prune -o "
        "-name 'photos-oxygen-sha*' -prune -o "
    )


@pytest.mark.parametrize(
    "root, message",
    [
        ("", "must not be empty"),
        ("   ", "must not be empty"),
        ("\0", "must not contain NUL"),
    ],
)
def test_remote_find_rejects_invalid_root(root: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        remote.remote_find("oxygen", root)


def test_ssh_stdout_rejects_non_utf8_output(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"\xff"),
    )

    with pytest.raises(ValueError, match="SSH output is not UTF-8"):
        remote.ssh_stdout("oxygen", "printf x")


def test_remote_find_handles_empty_archive(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b""),
    )

    assert remote.remote_find("oxygen", str(tmp_path)) == []


def test_remote_find_rejects_non_utf8_output(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"/photo/\xff\0"),
    )

    with pytest.raises(ValueError, match="discovery output is not UTF-8"):
        remote.remote_find("oxygen", "/photo")


def test_remote_find_preserves_newline_filenames(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=b"/var/services/photo/a\nb.jpg\0"
        ),
    )

    assert remote.remote_find("oxygen", "/var/services/photo") == [
        "/var/services/photo/a\nb.jpg"
    ]


@pytest.mark.parametrize("root", [".", "./"])
def test_remote_find_canonicalizes_current_directory_output(
    monkeypatch: Any, root: str
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"./photo.jpg\0"),
    )

    assert remote.remote_find("oxygen", root) == ["photo.jpg"]


def test_remote_find_normalizes_in_root_dot_path(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=b"/photo/./one.jpg\0/photo/one.jpg\0"
        ),
    )

    assert remote.remote_find("oxygen", "/photo") == ["/photo/one.jpg"]


def test_remote_find_normalizes_in_root_dotdot_path(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"/photo/album/../one.jpg\0"),
    )

    assert remote.remote_find("oxygen", "/photo") == ["/photo/one.jpg"]


def test_remote_find_rejects_dotdot_traversal(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"/photo/../other.jpg\0"),
    )

    with pytest.raises(ValueError, match="discovery path escapes root"):
        remote.remote_find("oxygen", "/photo")


def test_remote_find_rejects_root_prefix_collision(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"/photo2/file.jpg\0"),
    )

    with pytest.raises(ValueError, match="discovery path escapes root"):
        remote.remote_find("oxygen", "/photo")


def test_remote_find_rejects_outside_root_output(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(
        remote.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"/other/photo.jpg\0"),
    )

    with pytest.raises(ValueError, match="discovery path escapes root"):
        remote.remote_find("oxygen", str(tmp_path))


def test_remote_find_prunes_metadata_objects_and_ledgers(
    tmp_path: Path, monkeypatch: Any
) -> None:
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(stdout=b"/var/services/photo;echo unsafe/a.jpg\0")

    monkeypatch.setattr(remote.subprocess, "run", fake_run)

    assert remote.remote_find("oxygen", "/var/services/photo;echo unsafe") == [
        "/var/services/photo;echo unsafe/a.jpg"
    ]
    script = commands[0][-1]
    assert script.startswith("find '/var/services/photo;echo unsafe/' ")
    assert "-path '*/@eaDir' -prune -o" in script
    assert "-path '*/.objects' -prune -o" in script
    assert "-name 'photos-oxygen-sha*' -prune -o" in script


def test_paths_from_missing_tsv_and_relative_paths(tmp_path: Path) -> None:
    tsv = tmp_path / "missing.tsv"
    root = tmp_path / "originals"
    path = root / "A" / "photo.jpg"
    tsv.write_text(
        f"sha256\tcreated\tadjusted\tpath\nabc\t2024-01-01T00:00:00\t0\t{path}\n",
        encoding="utf-8",
    )

    records = remote.missing_records(tsv)
    paths = remote.paths_from_missing_tsv(tsv)

    assert records[0].year == "2024"
    assert paths == [path]
    assert remote.relative_paths(paths, root) == ["A/photo.jpg"]


def test_relative_paths_rejects_outside_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as error:
        remote.relative_paths([tmp_path / "other" / "photo.jpg"], tmp_path / "root")
    assert str(error.value) == (
        f"path is outside source root: {tmp_path / 'other' / 'photo.jpg'}"
    )


def test_missing_records_rejects_short_row(tmp_path: Path) -> None:
    tsv = tmp_path / "missing.tsv"
    tsv.write_text(
        "sha256\tcreated\tadjusted\tpath\nabc\t2024-01-01\t0\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        remote.missing_records(tsv)
    assert str(error.value) == "missing TSV row must have four columns"


def test_missing_records_rejects_invalid_adjusted_flag(tmp_path: Path) -> None:
    tsv = tmp_path / "missing.tsv"
    tsv.write_text(
        "sha256\tcreated\tadjusted\tpath\nabc\t2024-01-01\t2\t/photo.jpg\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError) as error:
        remote.missing_records(tsv)
    assert str(error.value) == "missing TSV adjusted flag must be 0 or 1"


def test_copy_stage_skips_empty_stage(tmp_path: Path, monkeypatch: Any) -> None:
    stage = tmp_path / "stage"
    (stage / "2025").mkdir(parents=True)
    run_calls: list[object] = []
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *args, **kwargs: run_calls.append(args)
    )
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    remote.copy_stage(stage, "oxygen", "/var/services/photo")
    assert run_calls == []


def test_copy_stage_skips_stage_of_only_symlinks(
    tmp_path: Path, monkeypatch: Any
) -> None:
    stage = tmp_path / "stage" / "2025"
    stage.mkdir(parents=True)
    target = tmp_path / "target.jpg"
    target.write_bytes(b"outside")
    (stage / "link.jpg").symlink_to(target)
    run_calls: list[object] = []
    monkeypatch.setattr(
        remote.subprocess, "run", lambda *args, **kwargs: run_calls.append(args)
    )
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    remote.copy_stage(tmp_path / "stage", "oxygen", "/var/services/photo")

    assert run_calls == []


def test_copy_stage_excludes_symlinked_files(tmp_path: Path, monkeypatch: Any) -> None:
    stage = tmp_path / "stage"
    year = stage / "2025"
    year.mkdir(parents=True)
    (year / "photo\nname.jpg").write_bytes(b"photo")
    external = tmp_path / "external.jpg"
    external.write_bytes(b"outside")
    (year / "link.jpg").symlink_to(external)
    external_year = tmp_path / "external-year"
    external_year.mkdir()
    (stage / "2024").symlink_to(external_year, target_is_directory=True)
    tar_files: list[str] = []
    run_commands: list[list[str]] = []

    class FakeTar:
        stdout = io.BytesIO()

        def wait(self) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: Any) -> FakeTar:
        list_path = Path(command[command.index("-T") + 1])
        tar_files.extend(list_path.read_bytes().decode().split("\0")[:-1])
        return FakeTar()

    monkeypatch.setattr(remote.subprocess, "Popen", fake_popen)

    def fake_run(command: list[str], **kwargs: Any) -> None:
        run_commands.append(command)

    monkeypatch.setattr(remote.subprocess, "run", fake_run)
    remote.copy_stage(stage, "oxygen", "/var/services/photo")

    assert tar_files == ["2025/photo\nname.jpg"]
    assert all("2024" not in " ".join(command) for command in run_commands)


def test_copy_paths_tar_preserves_newline_filename(
    tmp_path: Path, monkeypatch: Any
) -> None:
    tar_files: list[str] = []

    class FakeTar:
        stdout = io.BytesIO()

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def wait(self) -> int:
            return 0

    def fake_popen(command: list[str], **kwargs: Any) -> FakeTar:
        list_path = Path(command[command.index("-T") + 1])
        tar_files.extend(list_path.read_bytes().decode().split("\0")[:-1])
        assert "--null" in command
        return FakeTar()

    monkeypatch.setattr(remote.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(remote.subprocess, "run", lambda *args, **kwargs: None)

    assert (
        remote.copy_paths_tar(["album/photo\nname.jpg"], tmp_path, "oxygen", "/photo")
        == 1
    )
    assert tar_files == ["album/photo\nname.jpg"]


def test_cli_reports_filesystem_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            [
                "copy-missing",
                str(tmp_path / "missing.tsv"),
                str(tmp_path / "source"),
                "oxygen",
                "/photo",
                "--dry-run",
            ]
        )
        == 1
    )
    assert "photostow failed:" in capsys.readouterr().err


def test_copy_missing_dry_run_rejects_symlinked_parent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    path = external / "photo.jpg"
    path.write_bytes(b"outside")
    (source_root / "album").symlink_to(external, target_is_directory=True)
    tsv = tmp_path / "missing.tsv"
    tsv.write_text(
        "sha256\tcreated\tadjusted\tpath\n"
        f"abc\t2025-01-01T00:00:00\t0\t{source_root / 'album' / 'photo.jpg'}\n",
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "copy-missing",
                str(tsv),
                str(source_root),
                "oxygen",
                "/photo",
                "--dry-run",
            ]
        )
        == 1
    )
    assert (
        "photostow failed: source path escapes source root" in capsys.readouterr().err
    )


def test_validate_source_paths_accepts_relative_paths(tmp_path: Path) -> None:
    remote.validate_source_paths(["album/photo.jpg"], tmp_path)

    with pytest.raises(ValueError, match="must be relative"):
        remote.validate_source_paths([str(tmp_path / "photo.jpg")], tmp_path)

    root = tmp_path / "root"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "photo.jpg").write_bytes(b"outside")
    (root / "album").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes source root"):
        remote.validate_source_paths(["album/photo.jpg"], root)


def test_copy_missing_rejects_outside_record_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    tsv = tmp_path / "missing.tsv"
    outside = tmp_path / "outside" / "photo.jpg"
    tsv.write_text(
        f"sha256\tcreated\tadjusted\tpath\nabc\t2025-01-01T00:00:00\t0\t{outside}\n",
        encoding="utf-8",
    )

    assert (
        cli.main(
            [
                "copy-missing",
                str(tsv),
                str(tmp_path / "source"),
                "oxygen",
                "/photo",
                "--dry-run",
            ]
        )
        == 1
    )
    assert "photostow failed:" in capsys.readouterr().err


def test_copy_paths_tar_rejects_absolute_source_path(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be relative"):
        remote.copy_paths_tar(
            [str(tmp_path / "photo.jpg")], tmp_path, "oxygen", "/photo"
        )


def test_copy_paths_tar_rejects_symlink_source(
    tmp_path: Path, monkeypatch: Any
) -> None:
    target = tmp_path / "target.jpg"
    target.write_bytes(b"photo")
    (tmp_path / "link.jpg").symlink_to(target)
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    with pytest.raises(ValueError, match="refusing symlink source path"):
        remote.copy_paths_tar(["link.jpg"], tmp_path, "oxygen", "/photo")


def test_copy_paths_tar_rejects_symlinked_parent(
    tmp_path: Path, monkeypatch: Any
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "photo.jpg").write_bytes(b"outside")
    (root / "album").symlink_to(external, target_is_directory=True)
    monkeypatch.setattr(
        remote.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("spawned")),
    )

    with pytest.raises(ValueError, match="escapes source root"):
        remote.copy_paths_tar(["album/photo.jpg"], root, "oxygen", "/photo")


def test_stage_by_year_skips_symlink_sources(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    root.mkdir()
    target = root / "real.jpg"
    target.write_bytes(b"photo")
    source = root / "link.jpg"
    source.symlink_to(target)
    stage = tmp_path / "stage"
    record = remote.MissingRecord("abc123", "2025-02-03T00:00:00", False, source)

    assert remote.stage_by_year([record], root, stage) == 0
    assert not stage.exists()


def test_stage_by_year_skips_symlinked_parent_sources(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    (external / "photo.jpg").write_bytes(b"outside")
    (root / "album").symlink_to(external, target_is_directory=True)
    source = root / "album" / "photo.jpg"
    stage = tmp_path / "stage"
    record = remote.MissingRecord("abc123", "2025-02-03T00:00:00", False, source)

    assert remote.stage_by_year([record], root, stage) == 0
    assert not stage.exists()


def test_stage_by_year_rejects_symlink_destinations(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    root.mkdir()
    source = root / "photo.jpg"
    source.write_bytes(b"photo")
    record = remote.MissingRecord("abc123", "2025-02-03T00:00:00", False, source)

    stage = tmp_path / "stage"
    stage_target = tmp_path / "stage-target"
    stage_target.mkdir()
    stage.symlink_to(stage_target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink review stage"):
        remote.stage_by_year([record], root, stage)

    stage = tmp_path / "stage-real"
    stage.mkdir()
    year_target = tmp_path / "year-target"
    year_target.mkdir()
    (stage / "2025").symlink_to(year_target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink review year"):
        remote.stage_by_year([record], root, stage)


def test_stage_by_year_hardlinks_flat_year_dirs(tmp_path: Path) -> None:
    root = tmp_path / "originals"
    src = root / "A" / "photo.jpg"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"photo")
    stage = tmp_path / "stage"
    record = remote.MissingRecord("abc123", "2025-02-03T00:00:00", False, src)

    assert remote.stage_by_year([record], root, stage) == 1
    assert (stage / "2025" / "photo.jpg").read_bytes() == b"photo"


def test_prune_remote_ledger_removes_deleted_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = tmp_path / "photos-oxygen-sha"
    ledger.write_text(
        "oldhash  /var/services/photo/old.jpg\nnewhash  /var/services/photo/new.jpg\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        remote, "remote_find", lambda host, root: ["/var/services/photo/new.jpg"]
    )

    assert remote.prune_remote_ledger("oxygen", "/var/services/photo", ledger) == (2, 1)
    assert (
        ledger.read_text(encoding="utf-8") == "newhash  /var/services/photo/new.jpg\n"
    )


def test_update_remote_ledger_preserves_ledger_on_hash_rejection(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = tmp_path / "photos-oxygen-sha"
    original = "oldhash  /var/services/photo/old.jpg\n"
    ledger.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        remote,
        "remote_find",
        lambda host, root: ["/var/services/photo/a\nb.jpg"],
    )
    monkeypatch.setattr(
        remote,
        "remote_sha256",
        lambda host, paths: (_ for _ in ()).throw(
            ValueError("remote hash output contains unsupported newline path")
        ),
    )

    with pytest.raises(ValueError, match="unsupported newline path"):
        remote.update_remote_ledger("oxygen", "/var/services/photo", ledger)
    assert ledger.read_text(encoding="utf-8") == original


def test_update_remote_ledger_skips_hashing_without_new_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = tmp_path / "photos-oxygen-sha"
    original = "oldhash  /var/services/photo/old.jpg\n"
    ledger.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        remote, "remote_find", lambda host, root: ["/var/services/photo/old.jpg"]
    )
    monkeypatch.setattr(
        remote,
        "remote_sha256",
        lambda host, paths: (_ for _ in ()).throw(AssertionError("hashed")),
    )

    output = tmp_path / "output-ledger"
    assert (
        remote.update_remote_ledger(
            "oxygen", "/var/services/photo", ledger, output=output
        )
        == 0
    )
    assert ledger.read_text(encoding="utf-8") == original
    assert output.read_text(encoding="utf-8") == original


def test_update_remote_ledger_hashes_only_new_paths(
    tmp_path: Path, monkeypatch: Any
) -> None:
    ledger = tmp_path / "photos-oxygen-sha"
    ledger.write_text("oldhash  /var/services/photo/old.jpg\n", encoding="utf-8")

    monkeypatch.setattr(
        remote,
        "remote_find",
        lambda host, root: [
            "/var/services/photo/old.jpg",
            "/var/services/photo/new.jpg",
        ],
    )
    hashed: list[list[str]] = []

    def fake_sha256(host: str, paths: list[str]) -> str:
        hashed.append(paths)
        return "newhash  /var/services/photo/new.jpg\n"

    monkeypatch.setattr(remote, "remote_sha256", fake_sha256)

    assert remote.update_remote_ledger("oxygen", "/var/services/photo", ledger) == 1
    assert hashed == [["/var/services/photo/new.jpg"]]
    assert ledger.read_text(encoding="utf-8") == (
        "oldhash  /var/services/photo/old.jpg\nnewhash  /var/services/photo/new.jpg\n"
    )
