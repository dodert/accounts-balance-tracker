"""Multi-user mode: invitations, encrypted databases, passwords and the admin."""
import json
import re
import sqlite3
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import crypto
from app.config import Settings
from app.main import create_app
from app.totp import code_at
from app.users import USER_COOKIE, UserDatabases, db_aad, now

ADMIN = {"username": "admin", "password": "admin-pass"}


@pytest.fixture(autouse=True)
def fast_argon2(monkeypatch):
    # The real cost (64 MiB, 3 passes) makes each login take a noticeable time; the tests only need it to work
    monkeypatch.setattr(crypto, "ARGON2", {"time_cost": 1, "memory_cost": 8, "parallelism": 1})
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0)


def make_app(tmp_path):
    return create_app(Settings(data_dir=tmp_path, auth_username=ADMIN["username"], auth_password=ADMIN["password"],
                               multi_user=True))


def admin_client(app, tmp_path):
    client = TestClient(app)
    res = client.post("/login", data=ADMIN, follow_redirects=False)
    assert res.headers["location"] == "/login/totp"  # the one-time code is always asked to the admin
    secret = json.loads((tmp_path / ".totp_secret").read_text())["secret"]
    res = client.post("/login/totp", data={"code": code_at(secret, int(time.time() // 30))}, follow_redirects=False)
    assert res.headers["location"] == "/"
    return client


def invite(admin, email=""):
    page = admin.post("/admin/invitations", data={"email": email}).text
    return re.search(r"/invite/([\w-]+)", page).group(1)


def sign_up(app, token, username="ana", email="ana@example.com", password="ana-password"):
    client = TestClient(app)
    res = client.post(f"/invite/{token}", data={"username": username, "email": email, "password": password,
                                                "password2": password})
    return client, res


def recovery_key(res) -> str:
    return re.search(r'<code class="totp-secret">([A-Z2-7-]+)</code>', res.text).group(1)


@pytest.fixture
def setup(tmp_path):
    app = make_app(tmp_path)
    return app, admin_client(app, tmp_path)


def test_needs_the_admin_login(tmp_path):
    with pytest.raises(ValueError, match="MULTI_USER"):
        create_app(Settings(data_dir=tmp_path, multi_user=True))


def test_sign_up_with_an_invitation_then_use_the_app(setup, tmp_path):
    app, admin = setup
    token = invite(admin)
    client, res = sign_up(app, token)
    assert res.status_code == 200 and USER_COOKIE in client.cookies
    assert re.fullmatch(r"([A-Z2-7]{4}-){5}[A-Z2-7]{4}", recovery_key(res))
    assert client.get("/api/about").json()["user"] == "ana"
    assert client.get("/api/accounts").json() == []
    created = client.post("/api/accounts", json={"name": "Secret savings", "type": "savings"})
    assert created.status_code == 201
    client.post(f"/api/accounts/{created.json()['id']}/balances", json={"date": "2026-01-01", "amount": 1234.56})

    # The data is there after logging in again, from another browser
    other = TestClient(app)
    assert other.post("/login", data={"username": "ana", "password": "ana-password"},
                      follow_redirects=False).headers["location"] == "/"
    assert [a["name"] for a in other.get("/api/accounts").json()] == ["Secret savings"]

    # On disk it is encrypted: neither the user's file nor the system database holds it
    files = list((tmp_path / "users").glob("*.db.enc"))
    assert len(files) == 1
    for path in (files[0], tmp_path / "users.db"):
        assert b"Secret savings" not in path.read_bytes()
    assert not files[0].read_bytes().startswith(b"SQLite format 3")
    assert files[0].stat().st_mode & 0o777 == 0o600


def test_users_do_not_see_each_other(setup):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    bob, _ = sign_up(app, invite(admin), "bob", "bob@example.com", "bob-password")
    ana.post("/api/accounts", json={"name": "Ana's", "type": "bank"})
    bob.post("/api/accounts", json={"name": "Bob's", "type": "bank"})
    assert [a["name"] for a in ana.get("/api/accounts").json()] == ["Ana's"]
    assert [a["name"] for a in bob.get("/api/accounts").json()] == ["Bob's"]


def test_an_invitation_works_once(setup):
    app, admin = setup
    token = invite(admin)
    sign_up(app, token)
    _, again = sign_up(app, token, "bob", "bob@example.com")
    assert "register.error.invitation" in again.text
    assert TestClient(app).get(f"/invite/{token}").status_code == 404
    assert TestClient(app).get("/invite/made-up-token").status_code == 404


def expire_invitations(tmp_path):
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.execute("UPDATE invitations SET expires_at = ?", ((now() - timedelta(minutes=1)).isoformat(" "),))


def test_expired_invitation(setup, tmp_path):
    app, admin = setup
    token = invite(admin)
    expire_invitations(tmp_path)
    _, res = sign_up(app, token)
    assert "register.error.invitation" in res.text
    assert "No open invitations" in admin.get("/admin").text


def test_cancelled_invitation(setup, tmp_path):
    app, admin = setup
    token = invite(admin)
    with sqlite3.connect(tmp_path / "users.db") as db:
        invitation_id = db.execute("SELECT id FROM invitations").fetchone()[0]
    admin.post(f"/admin/invitations/{invitation_id}/cancel")
    _, res = sign_up(app, token)
    assert "register.error.invitation" in res.text


def test_invitation_for_one_email(setup, tmp_path):
    app, admin = setup
    token = invite(admin, "Carla@Example.com")
    assert 'value="carla@example.com"' in TestClient(app).get(f"/invite/{token}").text
    client, res = sign_up(app, token, "carla", "someone-else@example.com")
    assert USER_COOKIE in client.cookies
    # The email of the invitation is used, whatever was sent
    with sqlite3.connect(tmp_path / "users.db") as db:
        assert db.execute("SELECT email FROM users").fetchall() == [("carla@example.com",)]


def test_sign_up_checks(setup, tmp_path):
    app, admin = setup
    token = invite(admin)
    cases = [
        ({"username": "admin"}, "register.error.username"),
        ({"username": "a"}, "register.error.username"),
        ({"username": "has space"}, "register.error.username"),
        ({"email": "not-an-email"}, "register.error.email"),
        ({"password": "short", "password2": "short"}, "register.error.password"),
        ({"password2": "something else"}, "register.error.mismatch"),
    ]
    for change, error in cases:
        data = {"username": "ana", "email": "ana@example.com", "password": "ana-password", "password2": "ana-password"}
        data.update(change)
        res = TestClient(app).post(f"/invite/{token}", data=data)
        assert error in res.text, change
    # None of them used up the invitation
    client, _ = sign_up(app, token)
    assert USER_COOKIE in client.cookies
    # Name and email already taken
    _, res = sign_up(app, invite(admin), "ANA", "other@example.com")
    assert "register.error.taken" in res.text
    _, res = sign_up(app, invite(admin), "other", "ANA@example.com")
    assert "register.error.taken" in res.text
    # A blocked email can't sign up
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.execute("INSERT INTO blocked_emails (email, created_at) VALUES ('bad@example.com', '2026-01-01 00:00:00')")
    _, res = sign_up(app, invite(admin), "bad", "Bad@example.com")
    assert "register.error.blocked" in res.text


def test_wrong_password_and_unknown_user(setup):
    app, admin = setup
    sign_up(app, invite(admin))
    client = TestClient(app)
    for data in ({"username": "ana", "password": "wrong-password"}, {"username": "nobody", "password": "x" * 10}):
        res = client.post("/login", data=data, follow_redirects=False)
        assert res.headers["location"] == "/login?error=1" and USER_COOKIE not in res.cookies
    assert client.get("/api/accounts").status_code == 401


def test_change_password_keeps_the_data_and_logs_out_other_devices(setup):
    app, admin = setup
    phone, _ = sign_up(app, invite(admin))
    phone.post("/api/accounts", json={"name": "Kept", "type": "bank"})
    laptop = TestClient(app)
    laptop.post("/login", data={"username": "ana", "password": "ana-password"})

    wrong = laptop.post("/account/password", data={"current": "nope-nope-nope", "password": "new-password-1",
                                                   "password2": "new-password-1"})
    assert "account.error" in wrong.text
    res = laptop.post("/account/password", data={"current": "ana-password", "password": "new-password-1",
                                                 "password2": "new-password-1"}, follow_redirects=False)
    assert res.headers["location"] == "/account?changed=1"
    assert [a["name"] for a in laptop.get("/api/accounts").json()] == ["Kept"]  # this device stays logged in
    assert phone.get("/api/accounts").status_code == 401  # the other one is logged out

    fresh = TestClient(app)
    assert fresh.post("/login", data={"username": "ana", "password": "ana-password"},
                      follow_redirects=False).headers["location"] == "/login?error=1"
    assert fresh.post("/login", data={"username": "ana", "password": "new-password-1"},
                      follow_redirects=False).headers["location"] == "/"
    assert [a["name"] for a in fresh.get("/api/accounts").json()] == ["Kept"]


def test_recovery_key_sets_a_new_password(setup):
    app, admin = setup
    client, res = sign_up(app, invite(admin))
    key = recovery_key(res)
    client.post("/api/accounts", json={"name": "Kept", "type": "bank"})

    other = TestClient(app)
    bad = other.post("/recover", data={"username": "ana", "recovery_key": "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF",
                                       "password": "new-password-1", "password2": "new-password-1"})
    assert "recover.error" in bad.text
    # Lower case and without dashes is fine too
    ok = other.post("/recover", data={"username": "ana", "recovery_key": key.lower().replace("-", " "),
                                      "password": "new-password-1", "password2": "new-password-1"},
                    follow_redirects=False)
    assert ok.headers["location"] == "/"
    assert [a["name"] for a in other.get("/api/accounts").json()] == ["Kept"]
    assert client.get("/api/accounts").status_code == 401  # sessions with the old password end
    assert TestClient(app).post("/login", data={"username": "ana", "password": "new-password-1"},
                                follow_redirects=False).headers["location"] == "/"


def test_logout_ends_the_session_even_for_a_copied_cookie(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    cookie = client.cookies[USER_COOKIE]
    client.post("/logout")
    copy = TestClient(app)
    copy.cookies.set(USER_COOKIE, cookie)
    assert copy.get("/api/accounts").status_code == 401


def test_forged_or_damaged_cookies_are_rejected(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    cookie = client.cookies[USER_COOKIE]
    for bad in ("x", "", cookie[:-4] + "AAAA", "%%%", "A" * 5000):
        other = TestClient(app)
        other.cookies.set(USER_COOKIE, bad)
        assert other.get("/api/accounts").status_code == 401


def test_admin_pages_need_the_admin(setup):
    app, admin = setup
    user, _ = sign_up(app, invite(admin))
    assert user.get("/admin", follow_redirects=False).headers["location"] == "/login"
    assert user.post("/admin/invitations", data={}, follow_redirects=False).status_code == 303
    assert TestClient(app).get("/admin", follow_redirects=False).headers["location"] == "/login"
    # The admin has no data of its own
    assert admin.get("/api/accounts").status_code == 401
    assert admin.get("/", follow_redirects=False).headers["location"] == "/admin"


def test_multi_user_pages_do_not_exist_in_single_user_mode(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path)))
    for path in ("/invite/abc", "/recover", "/account", "/admin"):
        assert client.get(path).status_code == 404, path
    assert "/recover" not in client.get("/login").text


def test_an_older_user_database_is_backed_up_then_migrated(setup, tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from app.migrate import MIGRATIONS_DIR

    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    user_id = 1
    path = tmp_path / "users" / f"{user_id}.db.enc"
    # Replace the user's database with one at the previous schema, with data in it
    connection = sqlite3.connect(":memory:", check_same_thread=False)
    engine = create_engine("sqlite://", creator=lambda: connection, poolclass=StaticPool)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        config.attributes["connection"] = conn
        command.upgrade(config, "0004")
    connection.execute("INSERT INTO accounts (name, type, currency, archived) VALUES ('Old one', 'bank', 'EUR', 0)")
    connection.commit()
    databases = UserDatabases(tmp_path / "users")
    session = app_session(app, client)
    databases._write(user_id, session.key, connection.serialize())

    assert [a["name"] for a in client.get("/api/accounts").json()] == ["Old one"]
    backups = list((tmp_path / "users").glob(f"{user_id}.db.enc.backup-0004-to-*"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
    plain = crypto.unseal(session.key, path.read_bytes(), db_aad(user_id))
    migrated = sqlite3.connect(":memory:")
    migrated.deserialize(plain)
    assert migrated.execute("SELECT version_num FROM alembic_version").fetchone()[0] == databases.head


def app_session(app, client):
    """The logged-in session behind the client's cookie (to reach the user's key in a test)."""
    return app.state.users.session(client.cookies[USER_COOKIE])
