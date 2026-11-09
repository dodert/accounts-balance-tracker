"""Daily backups: a copy of the data in DATA_DIR/backups/<YYYY-MM-DD>/ once a day (UTC), the newest BACKUP_KEEP
days kept.

- Single user: balances.db, copied with SQLite's backup API (consistent, also while the app writes to it).
- Multi-user: users.db the same way, and each users/<id>.db.enc as it is (still encrypted), under that user's lock
  so the copy is never a file half-written. A day's users.db and .enc files go together: restore them together.

A day's folder is written under a temporary name and renamed when complete, so a crash never leaves a partial
backup that looks finished; the next run starts it again. The folder is 0700 and the files 0600.
"""
import logging
import re
import shutil
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

from .migrate import PRIVATE, _create_private

log = logging.getLogger(__name__)

FOLDER = "backups"
DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def backups_dir(data_dir: Path) -> Path:
    return data_dir / FOLDER


def _private_dir(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def copy_sqlite(source: Path, target: Path) -> None:
    """A consistent copy of a SQLite database (with what is in its WAL), readable only by the app's user."""
    _create_private(target)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    src.close()
    dst.close()
    target.chmod(PRIVATE)


def copy_private(source: Path, target: Path) -> None:
    _create_private(target)
    shutil.copyfile(source, target)
    target.chmod(PRIVATE)


def days(data_dir: Path) -> list[Path]:
    """The finished daily backups, oldest first."""
    root = backups_dir(data_dir)
    return sorted(p for p in root.iterdir() if p.is_dir() and DAY.match(p.name)) if root.is_dir() else []


def listing(data_dir: Path, user_id: int | None = None) -> list[dict]:
    """The finished daily backups, newest first: day (UTC), when it was made (UTC) and size in bytes. With a
    user id (multi-user), only the days with that user's copy, and only its size."""
    found = []
    for folder in reversed(days(data_dir)):
        files = [folder / "users" / f"{user_id}.db.enc"] if user_id is not None else \
            [p for p in folder.rglob("*") if p.is_file()]
        files = [p for p in files if p.is_file()]
        if not files:
            continue
        made = datetime.fromtimestamp(max(p.stat().st_mtime for p in files), UTC)
        found.append({"date": folder.name, "made_at": made.isoformat(timespec="seconds"),
                      "size": sum(p.stat().st_size for p in files)})
    return found


def make_daily_backup(data_dir: Path, keep: int, database: Path | None = None, users=None,
                      today: date | None = None) -> Path | None:
    """Today's backup, if there is none yet and backups are on (keep > 0); then only the newest `keep` days are kept.
    Returns the new folder, or None when there was nothing to do."""
    if keep <= 0:
        return None
    day = (today or datetime.now(UTC).date()).isoformat()
    root = backups_dir(data_dir)
    _private_dir(root)
    target = root / day
    if not target.exists():
        partial = root / f".{day}.partial"
        shutil.rmtree(partial, ignore_errors=True)  # left by a run cut short
        _private_dir(partial)
        try:
            if database is not None and database.exists():
                copy_sqlite(database, partial / database.name)
            if users is not None:
                users.backup_into(partial)
            partial.rename(target)
        except BaseException:
            shutil.rmtree(partial, ignore_errors=True)
            raise
        log.info("Daily backup made: %s", target)
    else:
        target = None
    for old in days(data_dir)[:-keep]:
        shutil.rmtree(old, ignore_errors=True)
        log.info("Old daily backup deleted: %s", old.name)
    return target
