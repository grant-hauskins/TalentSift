"""Back up or restore a TalentSift install (database + uploaded resumes).

    python scripts/backup.py                 # take a backup now and prune old ones
    python scripts/backup.py --list          # show existing backups
    python scripts/backup.py --verify FILE   # check that a backup is readable
    python scripts/backup.py --restore FILE  # stop the app first; current data is moved aside, not deleted

Settings come from .env: TALENTSIFT_DATA_DIR (or DATABASE_URL / UPLOAD_DIR), BACKUP_DIR, BACKUP_KEEP.
Cron example (every night at 02:00): see docs/install.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running from any folder

from talentsift.backup import BackupError, create_backup, list_backups, restore_backup, verify_backup  # noqa: E402
from talentsift.config import Settings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--list", action="store_true", help="list existing backups")
    group.add_argument("--verify", metavar="FILE", help="check that a backup archive is readable")
    group.add_argument("--restore", metavar="FILE", help="restore from a backup archive (stop the app first)")
    args = parser.parse_args(argv)
    settings = Settings.from_env()

    try:
        if args.list:
            backups = list_backups(settings)
            for path in backups:
                print(f"{path}  {path.stat().st_size / 1024:.0f} KB")
            print(f"{len(backups)} backup(s) in {settings.backup_dir} (keeping the newest {settings.backup_keep})")
        elif args.verify:
            print(f"OK: {args.verify} holds a readable database with {verify_backup(Path(args.verify))} tables.")
        elif args.restore:
            aside = restore_backup(settings, Path(args.restore))
            print(f"Restored from {args.restore}.")
            if aside:
                print(f"The data that was replaced is in {aside}.")
        else:
            archive = create_backup(settings)
            print(f"Backup written: {archive} ({archive.stat().st_size / 1024:.0f} KB)")
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
