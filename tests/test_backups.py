"""Daily backups in DATA_DIR/backups/<date>/ (backups.py)."""
import sqlite3
import time
from datetime import UTC, date, datetime

from fastapi.testclient import TestClient

from app.backups import days, make_daily_backup
from app.config import Settings
from app.main import create_app
from tests.test_multi_user import admin_client, fast_argon2, invite, make_app, sign_up  # noqa: F401 (fixture)


def utc_today() -> str:
    return datetime.now(UTC).date().isoformat()


def single_user(tmp_path, **settings):
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR", **settings)))
    account = client.post("/api/accounts", json={"name": "Savings"}).json()
    client.post(f"/api/accounts/{account['id']}/balances", json={"date": "2026-01-31", "amount": 123456})
    return client


def test_a_backup_is_made_on_start_with_all_the_data(tmp_path):
    """On start the app copies its database to backups/<today>/, private, with all the data."""
    single_user(tmp_path)
    folder = tmp_path / "backups" / utc_today()
    with TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"))):  # start: today's backup
        deadline = time.time() + 10  # made in the background, not to delay the start
        while not folder.exists():
            assert time.time() < deadline, "no backup made on start"
            time.sleep(0.05)
    copy = folder / "balances.db"
    with sqlite3.connect(copy) as db:
        assert db.execute("SELECT name FROM accounts").fetchall() == [("Savings",)]
        assert db.execute("SELECT amount FROM balances").fetchall() == [(1234.56,)]
    assert (tmp_path / "backups").stat().st_mode & 0o777 == 0o700
    assert folder.stat().st_mode & 0o777 == 0o700 and copy.stat().st_mode & 0o777 == 0o600


def test_one_backup_a_day_and_only_the_newest_kept(tmp_path):
    """A second run the same day does nothing; past BACKUP_KEEP days the oldest are deleted."""
    client = single_user(tmp_path, backup_keep=2)
    assert client.app.state.backup_now() is not None
    assert client.app.state.backup_now() is None  # already made today
    database = tmp_path / "balances.db"
    for day in ("2026-01-01", "2026-01-02", "2026-01-03"):
        make_daily_backup(tmp_path, 2, database, today=date.fromisoformat(day))
    assert [p.name for p in days(tmp_path)] == ["2026-01-03", utc_today()]
    assert not any(p.name.startswith(".") for p in (tmp_path / "backups").iterdir())  # no partial folder left


def test_the_api_lists_the_backups_made_newest_first(tmp_path):
    """GET /api/backups gives each finished day (newest first) with when it was made and its size."""
    client = single_user(tmp_path, backup_keep=3)
    database = tmp_path / "balances.db"
    for day in ("2026-01-01", "2026-01-02"):
        make_daily_backup(tmp_path, 3, database, today=date.fromisoformat(day))
    (tmp_path / "backups" / ".2026-01-03.partial").mkdir()  # unfinished: not listed
    data = client.get("/api/backups").json()
    assert data["keep"] == 3
    assert [b["date"] for b in data["backups"]] == ["2026-01-02", "2026-01-01"]
    first = data["backups"][0]
    assert first["size"] == (tmp_path / "backups" / "2026-01-02" / "balances.db").stat().st_size
    assert datetime.fromisoformat(first["made_at"]).tzinfo is not None


def test_no_daily_backups_with_backup_keep_0(tmp_path):
    """BACKUP_KEEP=0 turns the daily backups off."""
    single_user(tmp_path)
    with TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR", backup_keep=0))) as client:
        assert client.app.state.backup_now() is None
    assert not (tmp_path / "backups").exists()


def test_a_failed_backup_leaves_no_folder_that_looks_finished(tmp_path, monkeypatch):
    """A backup cut short leaves no day folder, so the next run makes it again."""
    client = single_user(tmp_path)
    import app.backups as backups

    def broken(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(backups, "copy_sqlite", broken)
    try:
        client.app.state.backup_now()
    except OSError:
        pass
    assert days(tmp_path) == []
    monkeypatch.undo()
    assert client.app.state.backup_now() is not None


def test_multi_user_backups_keep_the_files_encrypted_and_forget_deleted_users(tmp_path):
    """With several users, users.db and each encrypted file are copied as they are; a deleted user leaves no copy."""
    app = make_app(tmp_path)
    admin = admin_client(app, tmp_path)
    client, _ = sign_up(app, invite(admin))
    account = client.post("/api/accounts", json={"name": "Secret savings"}).json()
    client.post(f"/api/accounts/{account['id']}/balances", json={"date": "2026-01-01", "amount": 123456})
    folder = app.state.backup_now()
    original = next((tmp_path / "users").glob("*.db.enc"))
    copy = folder / "users" / original.name
    assert copy.read_bytes() == original.read_bytes() and b"Secret savings" not in copy.read_bytes()
    assert copy.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(folder / "users.db") as db:
        assert db.execute("SELECT username FROM users").fetchall() == [("ana",)]
    listed = client.get("/api/backups").json()["backups"]  # only this user's copy, not the others' files
    assert [(b["date"], b["size"]) for b in listed] == [(folder.name, copy.stat().st_size)]
    other, _ = sign_up(app, invite(admin), "eva", "eva@example.com")
    assert other.get("/api/backups").json()["backups"] == []  # no copy of eva's data yet
    app.state.users.delete_user(int(original.name.removesuffix(".db.enc")))
    assert not copy.exists()  # gone from the backups too
