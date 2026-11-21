import sqlite3
from datetime import date
from decimal import Decimal

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine

from app import models  # noqa: F401
from app.db import Base
from app.migrate import upgrade_database


def test_migrations_match_models(tmp_path):
    """The database migrations build exactly the tables of the models."""
    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    upgrade_database(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_upgrade_is_idempotent(tmp_path):
    """Upgrading an up-to-date database changes nothing and makes no backup."""
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
    """A database from a newer app version is refused and left untouched."""
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


def test_upgrade_to_contributions_keeps_the_data(tmp_path):
    """0006 adds the contributions table; the accounts, balances and milestones of a 0005 database stay."""
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
        command.upgrade(config, "0005")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'investment', 'EUR', 0)"))
        conn.execute(text("INSERT INTO balances (account_id, date, amount) VALUES (1, '2026-01-01', 5)"))
        conn.execute(text("INSERT INTO milestones (account_id, date, amount, description) VALUES (1, '2026-01-10', 300, 'Car')"))
    upgrade_database(engine)
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 1
        assert conn.scalar(text("SELECT count(*) FROM milestones")) == 1
        conn.execute(text("INSERT INTO contributions (account_id, date, amount) VALUES (1, '2026-01-02', 4)"))
        conn.execute(text("DELETE FROM accounts"))
        assert conn.scalar(text("SELECT count(*) FROM contributions")) == 0  # deleted with its account
    assert len(list(tmp_path.glob("db.sqlite.backup-0005-to-*"))) == 1


def test_upgrade_to_four_decimals_keeps_every_amount(tmp_path):
    """0007 rebuilds balances, milestones and contributions with 4 decimals; no amount or row of a 0006 database changes."""
    from decimal import Decimal

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import make_engine
    from app.migrate import MIGRATIONS_DIR
    from app.models import Balance, Contribution, Milestone

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")  # foreign keys on, like the app
    amounts = ["1234.50", "-0.01", "999999999.99", "0", "12.3"]
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0006")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'bank', 'EUR', 0)"))
        for n, amount in enumerate(amounts):
            conn.execute(text("INSERT INTO balances (account_id, date, amount, note) VALUES (1, :d, :a, 'n')"),
                         {"d": f"2026-01-0{n + 1}", "a": amount})
        conn.execute(text("INSERT INTO milestones (account_id, date, amount, description) VALUES (1, '2026-01-10', 300.25, 'Car')"))
        conn.execute(text("INSERT INTO contributions (account_id, date, amount, note) VALUES (1, '2025-12-01', -50.5, 'out')"))
    upgrade_database(engine)
    from sqlalchemy.orm import Session
    with Session(engine) as db:
        assert [b.amount for b in db.query(Balance).order_by(Balance.date)] == [Decimal(a) for a in amounts]
        assert [(m.amount, m.description) for m in db.query(Milestone)] == [(Decimal("300.25"), "Car")]
        assert [(c.amount, c.note) for c in db.query(Contribution)] == [(Decimal("-50.5"), "out")]
        db.add(Balance(account_id=1, date=Balance.date.type.python_type(2026, 2, 1), amount=Decimal("1.2345")))
        db.commit()
        assert db.query(Balance).filter(Balance.amount == Decimal("1.2345")).one().amount == Decimal("1.2345")
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM balances WHERE note = 'n'")) == len(amounts)
        assert conn.scalar(text("PRAGMA foreign_keys")) == 1
        conn.execute(text("DELETE FROM accounts"))
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 0  # the cascade still works
        assert conn.scalar(text("SELECT count(*) FROM milestones")) == 0
        assert conn.scalar(text("SELECT count(*) FROM contributions")) == 0
    assert len(list(tmp_path.glob("db.sqlite.backup-0006-to-*"))) == 1


def test_upgrade_to_mortgages_keeps_the_data(tmp_path):
    """0008 adds the mortgages and their prepayments; every account, balance, milestone and contribution of a 0007
    database stays, and a prepayment goes with its mortgage."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.db import make_engine
    from app.migrate import MIGRATIONS_DIR

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")  # the app's engine: foreign keys on
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0007")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'loan', 'EUR', 0)"))
        conn.execute(text("INSERT INTO balances (account_id, date, amount) VALUES (1, '2026-01-01', -150000.1234)"))
        conn.execute(text("INSERT INTO milestones (account_id, date, amount, description) VALUES (1, '2026-01-10', 300, 'Car')"))
        conn.execute(text("INSERT INTO contributions (account_id, date, amount) VALUES (1, '2026-01-02', 4)"))
    upgrade_database(engine)
    from sqlalchemy.orm import Session

    from app.models import Balance, Mortgage, Prepayment, PrepaymentMode
    with Session(engine) as db:
        assert [b.amount for b in db.query(Balance)] == [Decimal("-150000.1234")]
        db.add(Mortgage(name="Home", currency="EUR", principal=Decimal("150000"), rate=Decimal("3.1"),
                        discount=Decimal("0.6"), term_months=300, first_payment=date(2026, 1, 5), payment_day=5,
                        prepayments=[Prepayment(date=date(2026, 6, 1), amount=Decimal("5000"),
                                                mode=PrepaymentMode.reduce_term, fee=Decimal("0"))]))
        db.commit()
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT count(*) FROM milestones")) == 1
        assert conn.scalar(text("SELECT count(*) FROM contributions")) == 1
        conn.execute(text("DELETE FROM mortgages"))
        assert conn.scalar(text("SELECT count(*) FROM mortgage_prepayments")) == 0  # deleted with its mortgage
        assert conn.scalar(text("SELECT count(*) FROM balances")) == 1
    assert len(list(tmp_path.glob("db.sqlite.backup-0007-to-*"))) == 1


def test_upgrade_to_app_settings_keeps_the_data(tmp_path):
    """0009 adds the settings table (the guided tour seen); every account, balance, milestone, contribution and
    mortgage of a 0008 database stays."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.db import make_engine
    from app.migrate import MIGRATIONS_DIR
    from app.models import AppSetting

    engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")  # the app's engine: foreign keys on
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0008")
        conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'investment', 'EUR', 0)"))
        conn.execute(text("INSERT INTO balances (account_id, date, amount) VALUES (1, '2026-01-01', 1500.1234)"))
        conn.execute(text("INSERT INTO milestones (account_id, date, amount, description) VALUES (1, '2026-01-10', 300, 'Car')"))
        conn.execute(text("INSERT INTO contributions (account_id, date, amount) VALUES (1, '2026-01-01', 1000)"))
        conn.execute(text("INSERT INTO mortgages (id, name, currency, principal, rate, discount, term_months, "
                          "first_payment, payment_day) VALUES (1, 'Home', 'EUR', 150000, 3.1, 0.6, 300, '2026-01-05', 5)"))
        conn.execute(text("INSERT INTO mortgage_prepayments (mortgage_id, date, amount, mode, fee) "
                          "VALUES (1, '2026-06-01', 5000, 'reduce_term', 0)"))
    upgrade_database(engine)
    with Session(engine) as db:
        db.add(AppSetting(key="tour_seen", value="1"))
        db.commit()
    with engine.begin() as conn:
        assert conn.scalar(text("SELECT amount FROM balances")) == 1500.1234
        for table in ("accounts", "milestones", "contributions", "mortgages", "mortgage_prepayments", "app_settings"):
            assert conn.scalar(text(f"SELECT count(*) FROM {table}")) == 1, table
    assert len(list(tmp_path.glob("db.sqlite.backup-0008-to-*"))) == 1


def _mode(path):
    return path.stat().st_mode & 0o777


def test_new_database_is_private(tmp_path):
    """Balances are private: the database and its WAL files are 0600 even with the default umask (022)."""
    import os

    from sqlalchemy import text

    from app.db import make_engine

    old = os.umask(0o022)
    try:
        engine = make_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")  # WAL, like the app
        upgrade_database(engine)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO accounts (id, name, type, currency, archived) VALUES (1, 'A', 'bank', 'EUR', 0)"))
            files = list(tmp_path.iterdir())
            assert {p.name for p in files} >= {"db.sqlite", "db.sqlite-wal"}
            assert {p.name: _mode(p) for p in files} == {p.name: 0o600 for p in files}
    finally:
        os.umask(old)


def test_older_database_and_backups_are_made_private(tmp_path):
    """A database and backups left readable by others (644, older versions) are made private on start, and a new
    backup is created private."""
    from alembic import command
    from alembic.config import Config

    from app.migrate import MIGRATIONS_DIR

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}")
    with engine.begin() as conn:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_DIR))
        config.attributes["connection"] = conn
        command.upgrade(config, "0004")
    old_backup = tmp_path / "db.sqlite.backup-0001-to-0002-20260101-000000"
    old_backup.write_bytes(b"")
    for path in (tmp_path / "db.sqlite", old_backup):
        path.chmod(0o644)
    upgrade_database(engine)
    files = list(tmp_path.iterdir())
    assert len(list(tmp_path.glob("db.sqlite.backup-0004-to-*"))) == 1
    assert {p.name: _mode(p) for p in files} == {p.name: 0o600 for p in files}
