import logging
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


def backup_sqlite(engine: Engine, current: str, head: str) -> Path | None:
    """Consistent copy of the SQLite database (WAL included) next to it, before migrating it.
    Raises if it cannot be made, so the database is never migrated without a backup."""
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    source = Path(database)
    target = source.with_name(f"{source.name}.backup-{current}-to-{head}-{datetime.now():%Y%m%d-%H%M%S}")
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    src.close()
    dst.close()
    return target


def upgrade_database(engine: Engine) -> None:
    """Bring the schema to the latest revision (runs on every start).

    Before migrating an existing database, a SQLite one is backed up next to it (see backup_sqlite).

    SQLite changes a table by rebuilding it (copy, drop the old one, rename the copy). With foreign
    keys on, dropping `accounts` would delete every balance (ON DELETE CASCADE), so they are turned
    off while migrating, and checked afterwards. The pragma has no effect inside a transaction, so
    it is set before the migration's transaction starts."""
    sqlite = engine.dialect.name == "sqlite"
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
            if sqlite:
                backup = backup_sqlite(engine, current, head)
                if backup:
                    log.warning("Database backed up to %s before migrating it from %s to %s", backup, current, head)
            else:
                log.warning("Migrating the database from %s to %s (back it up with pg_dump before updating)", current, head)
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
