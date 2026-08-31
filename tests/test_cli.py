from pathlib import Path

import pytest

from photostow import cli





def test_documented_status_files_exist() -> None:
    root = Path(__file__).parents[1]
    assert (root / "TECHNICAL_REVIEW.md").is_file()
    assert (root / "ASSUMPTIONS.md").is_file()
    assert (root / "PROGRESS.md").is_file()


def test_main_reports_decode_failure(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def fail(args):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid")

    monkeypatch.setattr(cli, "cmd_hash", fail)

    assert cli.main(["hash", "/photo"]) == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("photostow failed: ")
    assert "invalid" in stderr
    assert "'utf-8' codec can't decode byte 0xff in position 0" in stderr


def test_main_reports_os_error(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def fail(args):
        raise OSError(13, "permission denied")

    monkeypatch.setattr(cli, "cmd_hash", fail)

    assert cli.main(["hash", "/photo"]) == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("photostow failed: ")
    assert "permission denied" in stderr
    assert "[Errno 13]" in stderr
    assert stderr == "photostow failed: [Errno 13] permission denied\n"


def test_main_reports_validation_failure(
    monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def fail(args):
        raise ValueError("remote discovery output is not UTF-8")

    monkeypatch.setattr(cli, "cmd_hash", fail)

    assert cli.main(["hash", "/photo"]) == 1
    assert capsys.readouterr().err == (
        "photostow failed: remote discovery output is not UTF-8\n"
    )
