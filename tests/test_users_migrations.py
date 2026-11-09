"""users.db (multi-user mode) migrated with its own Alembic tree (app/migrations_users)."""
import sqlite3

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.migrate import USERS_MIGRATIONS_DIR, alembic_config
from app.users import SystemBase, Users

SECRET = b"s" * 32
HEAD = ScriptDirectory.from_config(alembic_config(USERS_MIGRATIONS_DIR)).get_current_head()


def revision(path):
    with sqlite3.connect(path) as db:
        return db.execute("SELECT version_num FROM alembic_version").fetchone()[0]


def users_in(path):
    with sqlite3.connect(path) as db:
        return [row[0] for row in db.execute("SELECT username FROM users")]


def legacy_users_db(path, with_key_by_account):
    """A users.db as create_all made it before Alembic (no alembic_version), with one user."""
    Users(path.parent, SECRET, "admin")
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE alembic_version")
        db.execute("ALTER TABLE users DROP COLUMN totp_last_counter")  # added after Alembic (0003)
        if not with_key_by_account:
            db.execute("ALTER TABLE users DROP COLUMN key_by_account")
        db.execute("INSERT INTO users (username, email, created_at, blocked, kdf_salt, kdf_params, key_by_password,"
                   " recovery_salt, key_by_recovery, session_epoch) VALUES ('ana', 'ana@example.com',"
                   " '2026-11-08 10:00:00', 0, x'00', '{}', x'00', x'00', x'00', 0)")


def test_users_db_migrations_match_the_models(tmp_path):
    """The users.db migrations build exactly the tables of the models, and a new users.db is at the head."""
    Users(tmp_path, SECRET, "admin")
    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), SystemBase.metadata) == []
    assert revision(tmp_path / "users.db") == HEAD
    assert not list(tmp_path.glob("users.db.backup-*"))  # new: nothing to back up


def test_an_old_users_db_is_migrated_after_a_backup_keeping_its_users(tmp_path):
    """A users.db from before Alembic and before key_by_account is stamped 0001, backed up and migrated."""
    legacy_users_db(tmp_path / "users.db", with_key_by_account=False)
    Users(tmp_path, SECRET, "admin")
    assert revision(tmp_path / "users.db") == HEAD and users_in(tmp_path / "users.db") == ["ana"]
    [backup] = tmp_path.glob(f"users.db.backup-0001-to-{HEAD}-*")
    assert users_in(backup) == ["ana"] and backup.stat().st_mode & 0o777 == 0o600


def test_a_users_db_with_key_by_account_is_stamped_0002(tmp_path):
    """A users.db from before Alembic that has key_by_account is stamped 0002, then migrated from there."""
    legacy_users_db(tmp_path / "users.db", with_key_by_account=True)
    Users(tmp_path, SECRET, "admin")
    assert revision(tmp_path / "users.db") == HEAD and users_in(tmp_path / "users.db") == ["ana"]
    assert [p.name.split("-")[1:4] for p in tmp_path.glob("users.db.backup-*")] == [["0002", "to", HEAD]]
    Users(tmp_path, SECRET, "admin")  # a second start changes nothing and makes no other backup
    assert len(list(tmp_path.glob("users.db.backup-*"))) == 1


def test_a_users_db_from_a_newer_version_is_refused_unchanged(tmp_path):
    """A users.db migrated by a newer version of the app stops the start and is not changed."""
    Users(tmp_path, SECRET, "admin")
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.execute("UPDATE alembic_version SET version_num = '9999'")
    before = (tmp_path / "users.db").read_bytes()
    with pytest.raises(RuntimeError, match="newer version"):
        Users(tmp_path, SECRET, "admin")
    assert (tmp_path / "users.db").read_bytes() == before and revision(tmp_path / "users.db") == "9999"
