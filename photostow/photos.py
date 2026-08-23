from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from photostow.core import sha256_file

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class PhotoAsset:
    uuid: str
    filename: str
    directory: str
    created: datetime | None
    has_adjustments: bool
    path: Path

    @property
    def present(self) -> bool:
        return self.path.is_file()


def apple_timestamp(value: float | str | None) -> datetime | None:
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("Apple timestamp must be numeric") from error
    if not math.isfinite(seconds):
        raise ValueError("Apple timestamp must be finite")
    try:
        return APPLE_EPOCH + timedelta(seconds=seconds)
    except OverflowError as error:
        raise ValueError("Apple timestamp is out of range") from error


def photos_db(library: Path) -> Path:
    return library / "database" / "Photos.sqlite"


def connect_readonly(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)


def _asset_path(originals: Path, directory: str, filename: str) -> Path:
    if not directory:
        raise ValueError("Photos asset directory must not be empty")
    if not filename:
        raise ValueError("Photos asset filename must not be empty")
    path = originals / directory / filename
    try:
        path.resolve().relative_to(originals.resolve())
    except ValueError as error:
        raise ValueError(f"Photos asset path escapes originals: {path}") from error
    return path


def iter_assets(library: Path) -> list[PhotoAsset]:
    db = photos_db(library)
    originals = library / "originals"
    with connect_readonly(db) as conn:
        rows = conn.execute(
            """
            select ZUUID, ZFILENAME, ZDIRECTORY, ZDATECREATED, ZHASADJUSTMENTS
            from ZASSET
            where ZFILENAME is not null
              and ZDIRECTORY is not null
              and coalesce(ZTRASHEDSTATE, 0) = 0
            order by ZDATECREATED, ZUUID
            """
        )
        return [
            PhotoAsset(
                uuid=row[0],
                filename=row[1],
                directory=row[2],
                created=apple_timestamp(row[3]),
                has_adjustments=bool(row[4]),
                path=_asset_path(originals, row[2], row[1]),
            )
            for row in rows
        ]


def library_hashes(library: Path) -> list[tuple[str, PhotoAsset]]:
    return [
        (sha256_file(asset.path), asset)
        for asset in iter_assets(library)
        if asset.present
    ]


def missing_library_assets(
    library: Path, archived_hashes: set[str]
) -> list[tuple[str, PhotoAsset]]:
    return [
        (digest, asset)
        for digest, asset in library_hashes(library)
        if digest not in archived_hashes
    ]


def earliest_created(assets: list[PhotoAsset]) -> datetime | None:
    dates = [asset.created for asset in assets if asset.created is not None]
    return min(dates) if dates else None
