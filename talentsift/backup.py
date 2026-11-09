"""Backups for a self-hosted install: one archive holding the database and the uploaded resumes.

The database copy is taken with SQLite's online backup API, so it is consistent even while the app is
running or a screening run is writing. Archives are named by UTC time and the oldest are pruned.

    python scripts/backup.py              # take a backup now
    python scripts/backup.py --list
    python scripts/backup.py --restore data/backups/talentsift-20261112-020000.tar.gz

Schedule the first command with cron (see docs/install.md). Restoring never overwrites data silently:
the current database and uploads are moved aside first.
"""

from __future__ import annotations

import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from talentsift.config import Settings

PREFIX = "talentsift-"
SUFFIX = ".tar.gz"
DB_NAME = "talentsift.db"
UPLOADS_NAME = "uploads"


class BackupError(RuntimeError):
    """The backup or restore could not be completed; the message says why."""


def database_path(settings: Settings) -> Path:
    url = settings.database_url
    if not url.startswith("sqlite:///") or url == "sqlite:///:memory:":
        raise BackupError("Backups support a SQLite database file only (DATABASE_URL=sqlite:///...).")
    return Path(url.removeprefix("sqlite:///"))


def list_backups(settings: Settings) -> list[Path]:
    """Existing archives, oldest first."""
    if not settings.backup_dir.is_dir():
        return []
    return sorted(settings.backup_dir.glob(f"{PREFIX}*{SUFFIX}"))


def create_backup(settings: Settings, *, now: datetime | None = None) -> Path:
    """Write a new archive to `settings.backup_dir`, prune old ones, and return its path."""
    source = database_path(settings)
    if not source.exists():
        raise BackupError(f"No database found at {source}. Nothing to back up.")
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    archive = settings.backup_dir / f"{PREFIX}{stamp}{SUFFIX}"

    with tempfile.TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / DB_NAME
        with sqlite3.connect(source) as live, sqlite3.connect(snapshot) as copy:
            live.backup(copy)  # consistent snapshot, safe while the app is writing
        copy.close()
        live.close()
        partial = archive.with_suffix(".partial")
        with tarfile.open(partial, "w:gz") as tar:
            tar.add(snapshot, arcname=DB_NAME)
            if settings.upload_dir.is_dir():
                tar.add(settings.upload_dir, arcname=UPLOADS_NAME)
        partial.replace(archive)  # an interrupted backup never leaves a half-written archive behind

    prune_backups(settings)
    return archive


def prune_backups(settings: Settings) -> list[Path]:
    """Delete the oldest archives beyond `settings.backup_keep`. Returns what was deleted."""
    backups = list_backups(settings)
    doomed = backups[: max(0, len(backups) - settings.backup_keep)]
    for path in doomed:
        path.unlink()
    return doomed


def verify_backup(archive: Path) -> int:
    """Check that the archive holds a readable database. Returns the number of tables in it."""
    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(archive, "r:gz") as tar:
            try:
                member = tar.getmember(DB_NAME)
            except KeyError:
                raise BackupError(f"{archive.name} does not contain {DB_NAME}.") from None
            tar.extract(member, scratch, filter="data")
        connection = sqlite3.connect(Path(scratch) / DB_NAME)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError(f"The database inside {archive.name} is damaged.")
            return connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        finally:
            connection.close()


def restore_backup(settings: Settings, archive: Path, *, now: datetime | None = None) -> Path | None:
    """Replace the database and uploads with the archive's contents. Stop the app first.

    The current database and uploads are moved to `<backup_dir>/replaced-<time>/` rather than deleted.
    Returns that folder, or None if there was nothing to move.
    """
    archive = Path(archive)
    if not archive.is_file():
        raise BackupError(f"Backup not found: {archive}")
    verify_backup(archive)
    target = database_path(settings)
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    aside = settings.backup_dir / f"replaced-{stamp}"

    moved = False
    for path in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
        if path.exists():
            aside.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), aside / path.name)
            moved = True
    if settings.upload_dir.is_dir() and any(settings.upload_dir.iterdir()):
        aside.mkdir(parents=True, exist_ok=True)
        shutil.move(str(settings.upload_dir), aside / UPLOADS_NAME)
        moved = True

    with tempfile.TemporaryDirectory() as scratch:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(scratch, filter="data")  # "data" refuses paths that escape the folder
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(Path(scratch) / DB_NAME), target)
        uploads = Path(scratch) / UPLOADS_NAME
        if uploads.is_dir():
            settings.upload_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(uploads), settings.upload_dir)
    return aside if moved else None
