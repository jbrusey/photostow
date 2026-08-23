from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from photostow.photos import (
    apple_timestamp,
    earliest_created,
    iter_assets,
    missing_library_assets,
)


def make_library(tmp_path: Path) -> Path:
    library = tmp_path / "Photos Library.photoslibrary"
    (library / "database").mkdir(parents=True)
    (library / "originals" / "A").mkdir(parents=True)
    db = library / "database" / "Photos.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            create table ZASSET (
                ZUUID text,
                ZFILENAME text,
                ZDIRECTORY text,
                ZDATECREATED real,
                ZHASADJUSTMENTS integer,
                ZTRASHEDSTATE integer
            )
            """
        )
        conn.executemany(
            "insert into ZASSET values (?, ?, ?, ?, ?, ?)",
            [
                ("kept", "kept.jpg", "A", 0, 1, 0),
                ("missing", "missing.jpg", "A", 10, 0, 0),
                ("trashed", "trashed.jpg", "A", 20, 0, 1),
            ],
        )
    (library / "originals" / "A" / "kept.jpg").write_bytes(b"kept")
    return library


def test_apple_timestamp_rejects_out_of_range() -> None:
    with pytest.raises(ValueError) as error:
        apple_timestamp(1e300)
    assert str(error.value) == "Apple timestamp is out of range"


def test_apple_timestamp_rejects_non_numeric() -> None:
    with pytest.raises(ValueError) as error:
        apple_timestamp("not-a-number")
    assert str(error.value) == "Apple timestamp must be numeric"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_apple_timestamp_rejects_non_finite(value: float) -> None:
    with pytest.raises(ValueError) as error:
        apple_timestamp(value)
    assert str(error.value) == "Apple timestamp must be finite"


def test_apple_timestamp_uses_photos_epoch() -> None:
    assert apple_timestamp(0) == datetime(2001, 1, 1, tzinfo=timezone.utc)


def test_iter_assets_opens_special_character_library_path(tmp_path: Path) -> None:
    library = make_library(tmp_path / "library?name#1")

    assert [asset.uuid for asset in iter_assets(library)] == ["kept", "missing"]


def test_iter_assets_reads_paths_and_skips_trashed(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    assets = iter_assets(library)

    assert [asset.uuid for asset in assets] == ["kept", "missing"]
    assert assets[0].path == library / "originals" / "A" / "kept.jpg"
    assert assets[0].present is True
    assert assets[0].has_adjustments is True
    assert assets[1].present is False


def test_iter_assets_rejects_path_outside_originals(tmp_path: Path) -> None:
    library = make_library(tmp_path)
    with sqlite3.connect(library / "database" / "Photos.sqlite") as conn:
        conn.execute(
            "update ZASSET set ZDIRECTORY = ? where ZUUID = ?",
            ("/outside", "kept"),
        )

    with pytest.raises(ValueError) as error:
        iter_assets(library)
    assert str(error.value).startswith("Photos asset path escapes originals:")


@pytest.mark.parametrize(
    ("column", "message"),
    [
        ("ZDIRECTORY", "Photos asset directory must not be empty"),
        ("ZFILENAME", "Photos asset filename must not be empty"),
    ],
)
def test_iter_assets_rejects_empty_path_components(
    tmp_path: Path, column: str, message: str
) -> None:
    library = make_library(tmp_path)
    with sqlite3.connect(library / "database" / "Photos.sqlite") as conn:
        conn.execute(f"update ZASSET set {column} = '' where ZUUID = 'kept'")

    with pytest.raises(ValueError) as error:
        iter_assets(library)
    assert str(error.value) == message


def test_missing_library_assets_uses_hashes(tmp_path: Path) -> None:
    library = make_library(tmp_path)

    missing = missing_library_assets(library, archived_hashes=set())

    assert [(asset.uuid, digest) for digest, asset in missing] == [
        ("kept", "79f076abdd19a752db7267bfff2f9022161d120dea919fdaca2ffdfc24ca8c96")
    ]


def test_earliest_created_ignores_missing_dates(tmp_path: Path) -> None:
    assets = iter_assets(make_library(tmp_path))

    assert earliest_created(assets) == datetime(2001, 1, 1, tzinfo=timezone.utc)
