from pathlib import Path

import pytest

from photostow import cli


def test_review_keeps_live_acceptance_pending() -> None:
    review = (Path(__file__).parents[1] / "TECHNICAL_REVIEW.md").read_text(
        encoding="utf-8"
    )
    assert "- [ ] Run a small real-year migration" in review
    assert "- [ ] If `.objects` is external, run GC with explicit `--root`" in review
    assert "- [ ] Confirm object and visible hardlink counts before and after" in review
    assert "- [ ] Test Synology indexing" in review
    assert "- [ ] Test Pixette/WebDAV" in review
    assert "- [ ] Record permissions, ACLs, ownership, timestamps, and xattrs" in review
    assert "- [ ] Confirm the no-in-place-edit policy" in review
    assert "- [ ] After `make archive-reviewed`" in review
    assert "- [ ] Confirm RAID-0 is not treated as backup" in review
    assert review.count("- [ ]") == 9


def test_review_separates_local_and_production_status() -> None:
    review = (Path(__file__).parents[1] / "TECHNICAL_REVIEW.md").read_text(
        encoding="utf-8"
    )
    assert "- [x] `make review` passes" in review
    assert review.count("- [x]") == 2
    assert review.count("- [ ]") == 9


def test_readme_names_documented_status_test() -> None:
    readme = Path(__file__).parents[1] / "README.md"
    test_name = "test_documented_status_files_exist"
    readme_text = readme.read_text(encoding="utf-8")
    assert test_name in readme_text
    assert "Passing local `make review` is not production approval" in readme_text
    assert (
        "Production Oxygen/Synology acceptance remains pending because the environment is unavailable"
        in readme_text
    )
    review = Path(__file__).parents[1] / "TECHNICAL_REVIEW.md"
    review_text = review.read_text(encoding="utf-8")
    assert (
        "production Oxygen/Synology acceptance remains pending because the environment is unavailable"
        in review_text
    )
    assert test_name in review_text
    assert "test_documented_status_files_exist`" in review_text
    assert "passing local `make review` is not production approval" in review_text
    assert "photostow failed: remote discovery output is not UTF-8" in readme.read_text(
        encoding="utf-8"
    )
    review_text = review.read_text(encoding="utf-8")
    retry_phrase = "unchanged input is not retried automatically"
    assert retry_phrase in review_text
    assert "correct the remote transport before rerunning" in readme.read_text(
        encoding="utf-8"
    )
    assert "transport failures require correction before rerunning" in review_text
    retry_boundary = "before rerunning"
    assert retry_boundary in readme.read_text(encoding="utf-8")
    assert retry_boundary in review_text
    assert "[README retry guidance](README.md)" in review_text
    readme_text = readme.read_text(encoding="utf-8")
    assert (
        "filesystem failures return exit 1 with a concise stderr diagnostic"
        in readme_text
    )
    exception_phrase = "ValueError`, `UnicodeDecodeError`, and `OSError`"
    assert exception_phrase in readme_text
    assert exception_phrase in review_text
    assert "codec, byte position, and reason details" in readme_text
    assert "codec, byte position, and decode message" in review_text
    assert "test_main_reports_os_error" in readme_text
    errno_line = "[Errno 13]"
    assert errno_line in readme_text
    assert errno_line in review_text
    assert "[TECHNICAL_REVIEW.md](TECHNICAL_REVIEW.md)" in readme_text
    assert "[ASSUMPTIONS.md](ASSUMPTIONS.md)" in readme_text
    assert "No production migration is approved by local tests alone" in review_text
    assert "test_main_reports_os_error" in review_text
    assert "[Errno 13]" in review_text
    assert "UnicodeDecodeError" in review_text
    assert "OSError" in review_text
    assert "ValueError" in review_text
    assert "local tests alone do not approve a production migration" in readme_text
    assert "update `README.md` and `TECHNICAL_REVIEW.md` together" in readme_text
    assert "tests/test_cli.py" in readme_text
    assert "README.md`, `TECHNICAL_REVIEW.md`, and" in review_text
    assert "](tests/test_cli.py)" in readme_text
    assert "](tests/test_cli.py)" in review_text
    progress = Path(__file__).parents[1] / "PROGRESS.md"
    assert "Iteration 473" in progress.read_text(encoding="utf-8")
    progress_text = progress.read_text(encoding="utf-8")
    assert "tests/test_cli.py" in progress_text
    assert "iteration 473" in review_text
    assert "[PROGRESS.md](PROGRESS.md)" in review_text
    assert "[PROGRESS.md](PROGRESS.md)" in readme_text
    assert "repair a stale history link" in readme_text
    assert "restoring `PROGRESS.md` and rerunning the focused CLI test" in readme_text
    assert "restore `PROGRESS.md` and rerun the focused CLI test" in review_text


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
