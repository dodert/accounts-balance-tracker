import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
log = logging.getLogger(__name__)
PRIVATE = 0o600  # balances are private: only the app's user can read the database and its backups


def _sqlite_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if engine.dialect.name != "sqlite" or not database or database == ":memory:":
        return None
    return Path(database)


def _create_private(path: Path) -> None:
    """Create the file empty with PRIVATE permissions (whatever the umask), so it is never readable by others."""
    if not path.exists():
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, PRIVATE))


def restrict_permissions(path: Path) -> None:
    """Make the SQLite database, its WAL files and its backups readable only by the app's user (also for databases
    created by older versions with the default umask, 644). SQLite creates the WAL files with the database's mode."""
    files = [path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm"),
             *path.parent.glob(f"{path.name}.backup-*")]
    for file in files:
        try:
            if file.is_file() and file.stat().st_mode & 0o777 != PRIVATE:
                file.chmod(PRIVATE)
        except OSError as error:
            log.warning("Could not make %s private: %s", file, error)


def backup_sqlite(engine: Engine, current: str, head: str) -> Path | None:
    """Consistent copy of the SQLite database (WAL included) next to it, before migrating it.
    Raises if it cannot be made, so the database is never migrated without a backup."""
    return _backup_sqlite(engine, f"{current}-to-{head}")


# Backups made before an import replaces all the data: the latest ones are kept, older ones deleted
IMPORT_BACKUP = "before-import"
KEEP_IMPORT_BACKUPS = 3


def prune_backups(folder: Path, pattern: str, keep: int = KEEP_IMPORT_BACKUPS) -> None:
    """Delete all but the `keep` newest files matching `pattern` (their names end with the date)."""
    for old in sorted(folder.glob(pattern))[:-keep]:
        old.unlink(missing_ok=True)


def backup_sqlite_before_import(engine: Engine) -> Path | None:
    """Copy of the SQLite database before an import replaces everything in it (None if it is not SQLite)."""
    target = _backup_sqlite(engine, IMPORT_BACKUP)
    if target is not None:
        prune_backups(target.parent, f"{target.name.rsplit(IMPORT_BACKUP, 1)[0]}{IMPORT_BACKUP}-*")
    return target


def _backup_sqlite(engine: Engine, label: str) -> Path | None:
    source = _sqlite_path(engine)
    if source is None:
        return None
    target = source.with_name(f"{source.name}.backup-{label}-{datetime.now():%Y%m%d-%H%M%S}")
    _create_private(target)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    src.close()
    dst.close()
    return target


def upgrade_database(engine: Engine) -> None:
    """Bring the schema to the latest revision (runs on every start).

    Before migrating an existing database, a SQLite one is backed up next to it (see backup_sqlite).
    A SQLite database and its backups are kept private (0600, see restrict_permissions).

    SQLite changes a table by rebuilding it (copy, drop the old one, rename the copy). With foreign
    keys on, dropping `accounts` would delete every balance (ON DELETE CASCADE), so they are turned
    off while migrating, and checked afterwards. The pragma has no effect inside a transaction, so
    it is set before the migration's transaction starts."""
    sqlite = engine.dialect.name == "sqlite"
    database = _sqlite_path(engine)
    if database is not None:
        if database.parent.is_dir():
            _create_private(database)
        restrict_permissions(database)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
        connection.rollback()
        if current is not None and current not in {s.revision for s in scripts.walk_revisions()}:
            # Made by a newer version of the app (e.g. a branch build): this one cannot use it. Nothing is changed.
            raise RuntimeError(
                f"The database is from a newer version of the app (schema {current}; this version knows up to {head}). "
                "Run that newer version (or a later one), or restore a backup made before it migrated "
                "(balances.db.backup-* in the data folder). The database was not changed.")
        if current is not None and current != head:
            backup = backup_sqlite(engine, current, head)
            if backup:
                log.warning("Database backed up to %s before migrating it from %s to %s", backup, current, head)
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if sqlite and connection.execute(text("PRAGMA foreign_key_check")).first():
                    raise RuntimeError("Migration left balances without an account; nothing was changed")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
