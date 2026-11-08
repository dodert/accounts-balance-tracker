import sqlite3

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app import models  # noqa: F401
from app.db import Base
from app.migrate import upgrade_database


def test_migrations_match_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    upgrade_database(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    upgrade_database(engine)
    upgrade_database(engine)
    assert [p.name for p in tmp_path.iterdir()] == ["db.sqlite"]  # nothing to back up: new, then up to date


def test_upgrade_keeps_the_balances(tmp_path):
    """SQLite rebuilds `accounts` in 0002: the balances (ON DELETE CASCADE) must survive it."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import make_engine
    from app.migrate import MIGRATIONS_DIR

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")  # foreign keys on, like the app
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0001")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'bank', 'EUR', 0)"))
        conn.execute(text("INSERT INTO balances (account_id, date, amount) VALUES (1, '2026-01-01', 5)"))
    upgrade_database(engine)
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 1
        assert conn.scalar(text("SELECT available_from FROM accounts")) is None  # 0003: available
        assert conn.scalar(text("PRAGMA foreign_keys")) == 1
        conn.execute(text("DELETE FROM accounts"))
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 0  # the cascade still works

    # The database was backed up, as it was, before migrating it
    backups = list(tmp_path.glob("db.sqlite.backup-0001-to-*"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:
        assert old.execute("SELECT version_num FROM alembic_version").fetchone() == ("0001",)
        assert old.execute("SELECT count(*) FROM balances").fetchone() == (1,)
    old.close()


def test_database_from_a_newer_version_is_refused_untouched(tmp_path):
    import pytest
    from sqlalchemy import text

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    upgrade_database(engine)
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = '9999'"))
    with pytest.raises(RuntimeError, match="newer version of the app"):
        upgrade_database(engine)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT version_num FROM alembic_version")) == "9999"
    assert [p.name for p in tmp_path.iterdir()] == ["db.sqlite"]  # no backup, nothing changed


def test_upgrade_to_milestones_keeps_the_data(tmp_path):
    """0005 adds the milestones table; the accounts and balances of a 0004 database stay."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import make_engine
    from app.migrate import MIGRATIONS_DIR

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0004")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'bank', 'EUR', 0)"))
        conn.execute(text("INSERT INTO balances (account_id, date, amount) VALUES (1, '2026-01-01', 5)"))
    upgrade_database(engine)
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 1
        conn.execute(text("INSERT INTO milestones (account_id, date, amount, description) VALUES (1, '2026-01-10', 300, 'Car')"))
        conn.execute(text("DELETE FROM accounts"))
        assert conn.scalar(text("SELECT count(*) FROM milestones")) == 0  # deleted with its account
    assert len(list(tmp_path.glob("db.sqlite.backup-0004-to-*"))) == 1
