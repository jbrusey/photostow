from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from photostow.core import sha256_file

APPLE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


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


def apple_timestamp(value: float | None) -> datetime | None:
    if value is None:
        return None
    return APPLE_EPOCH + timedelta(seconds=float(value))


def photos_db(library: Path) -> Path:
    return library / "database" / "Photos.sqlite"


def connect_readonly(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


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
                path=originals / row[2] / row[1],
            )
            for row in rows
        ]


def library_hashes(library: Path) -> list[tuple[str, PhotoAsset]]:
    return [(sha256_file(asset.path), asset) for asset in iter_assets(library) if asset.present]


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
