"""Backup and restore of the database and uploaded resumes (T14)."""

from datetime import datetime, timezone

import pytest
from sqlmodel import select

from talentsift.backup import BackupError, create_backup, list_backups, restore_backup, verify_backup
from talentsift.config import Settings
from talentsift.db import create_db_engine, init_db, new_session
from talentsift.models import Role

from tests.factories import make_role


@pytest.fixture
def install(tmp_path):
    settings = Settings.from_env({"TALENTSIFT_DATA_DIR": str(tmp_path / "data"), "BACKUP_KEEP": "2"})
    engine = create_db_engine(settings.database_url)
    init_db(engine)
    with new_session(engine) as session:
        make_role(session, title="Before backup")
    settings.upload_dir.mkdir(parents=True)
    (settings.upload_dir / "resume.pdf").write_bytes(b"%PDF-1.4 fictional")
    yield settings, engine
    engine.dispose()


def at(hour):
    return datetime(2026, 11, 12, hour, tzinfo=timezone.utc)


def test_data_dir_setting_moves_database_uploads_and_backups_together(tmp_path):
    settings = Settings.from_env({"TALENTSIFT_DATA_DIR": str(tmp_path / "srv")})
    assert settings.database_url == f"sqlite:///{tmp_path / 'srv' / 'talentsift.db'}"
    assert settings.upload_dir == tmp_path / "srv" / "resumes" / "uploads"
    assert settings.backup_dir == tmp_path / "srv" / "backups"


def test_backup_is_a_readable_snapshot_taken_while_the_app_is_open(install):
    settings, _engine = install  # the engine still holds the database open, in WAL mode
    archive = create_backup(settings, now=at(2))
    assert archive.name == "talentsift-20261112-020000.tar.gz"
    assert verify_backup(archive) >= 10
    assert not list(settings.backup_dir.glob("*.partial"))


def test_old_backups_are_pruned(install):
    settings, _ = install
    names = [create_backup(settings, now=at(hour)).name for hour in (1, 2, 3)]
    assert [p.name for p in list_backups(settings)] == names[1:]


def test_restore_brings_back_the_data_and_keeps_what_it_replaced(install):
    settings, engine = install
    archive = create_backup(settings, now=at(2))
    with new_session(engine) as session:
        make_role(session, title="After backup")
    (settings.upload_dir / "later.pdf").write_bytes(b"%PDF-1.4 later")
    engine.dispose()  # stop the app before restoring

    aside = restore_backup(settings, archive, now=at(5))
    assert aside is not None and (aside / "talentsift.db").exists() and (aside / "uploads" / "later.pdf").exists()
    restored = create_db_engine(settings.database_url)
    with new_session(restored) as session:
        assert [r.title for r in session.exec(select(Role))] == ["Before backup"]
    restored.dispose()
    assert sorted(p.name for p in settings.upload_dir.iterdir()) == ["resume.pdf"]


def test_clear_errors(install, tmp_path):
    settings, _ = install
    with pytest.raises(BackupError, match="not found"):
        restore_backup(settings, tmp_path / "missing.tar.gz")
    with pytest.raises(BackupError, match="SQLite database file"):
        create_backup(settings.with_overrides(database_url="sqlite://"))
    with pytest.raises(BackupError, match="Nothing to back up"):
        create_backup(settings.with_overrides(database_url=f"sqlite:///{tmp_path / 'none.db'}"))
