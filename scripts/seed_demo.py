"""Load the fictional demo roles and resumes into the database (re-run after a hosted reboot).

    python scripts/seed_demo.py                       # roles + resumes, offline fake client
    python scripts/seed_demo.py --screen              # ...and screen both roles
    python scripts/seed_demo.py --screen --provider openrouter   # draft and screen with the real model
    python scripts/seed_demo.py --reset --screen      # start from a fresh database

--reset never deletes audit history: the old database file is moved to data/backups/.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running from any folder

from sqlmodel import select  # noqa: E402

from talentsift.config import DATA_DIR, Settings  # noqa: E402
from talentsift.db import create_db_engine, init_db, new_session  # noqa: E402
from talentsift.llm import LLMError, build_client  # noqa: E402
from talentsift.models import Applicant, Evaluation, Role, ScreeningRun  # noqa: E402
from talentsift.demo import seed_demo  # noqa: E402


def backup_database(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        raise SystemExit("--reset only supports SQLite databases")
    path = Path(url.removeprefix("sqlite:///"))
    if not path.exists():
        return None
    backups = DATA_DIR / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    target = backups / f"{path.stem}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}{path.suffix}"
    shutil.move(str(path), target)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            shutil.move(str(sidecar), f"{target}{suffix}")
    return target


def print_run(session, run_id: int) -> None:
    run = session.get(ScreeningRun, run_id)
    role = session.get(Role, run.role_id)
    print(f"\nRun {run.id}: {role.title} v{run.role_version} · {run.status} · model {run.model_requested} · "
          f"{run.total_tokens:,} tokens · ${run.total_cost:.4f}")
    rows = session.exec(select(Evaluation).where(Evaluation.run_id == run.id))
    ordered = sorted(rows, key=lambda e: (e.rank is None, e.rank or 0, e.applicant_id))
    for e in ordered:
        applicant = session.get(Applicant, e.applicant_id)
        score = "  -  " if e.fit_score is None else f"{e.fit_score:5.1f}"
        rank = f"#{e.rank:<2}" if e.rank else "   "
        print(f"  {rank} {applicant.display_label:13} {score}  {e.status:16} {applicant.original_filename}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--provider", choices=["fake", "openrouter"], default="fake")
    parser.add_argument("--screen", action="store_true", help="also screen every sample resume for both roles")
    parser.add_argument("--reset", action="store_true", help="move the current database to data/backups first")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.reset:
        moved = backup_database(settings.database_url)
        print(f"Moved the old database to {moved}" if moved else "No existing database to move.")
    try:
        client = build_client(settings, args.provider)
    except LLMError as exc:
        print(f"Cannot use {args.provider}: {exc}")
        return 1

    engine = create_db_engine(settings.database_url)
    init_db(engine)
    with new_session(engine) as session:
        summary = seed_demo(session, settings, client=client, screen=args.screen)
        print(summary.message)
        for run_id in summary.run_ids:
            print_run(session, run_id)
    return 1 if summary.errors else 0


if __name__ == "__main__":
    sys.exit(main())
