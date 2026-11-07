from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import Engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def upgrade_database(engine: Engine) -> None:
    """Bring the schema to the latest revision (runs on every start).

    SQLite changes a table by rebuilding it (copy, drop the old one, rename the copy). With foreign
    keys on, dropping `accounts` would delete every balance (ON DELETE CASCADE), so they are turned
    off while migrating, and checked afterwards. The pragma has no effect inside a transaction, so
    it is set before the migration's transaction starts."""
    sqlite = engine.dialect.name == "sqlite"
    with engine.connect() as connection:
        if sqlite:
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        try:
            with connection.begin():
                config = Config()
                config.set_main_option("script_location", str(MIGRATIONS_DIR))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                if sqlite and connection.execute(text("PRAGMA foreign_key_check")).first():
                    raise RuntimeError("Migration left balances without an account; nothing was changed")
        finally:
            if sqlite:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()
