"""Multi-user mode: invitations, encrypted databases, passwords and the admin."""
import json
import re
import sqlite3
import time
from datetime import date, timedelta

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


def make_app(tmp_path, **settings):
    return create_app(Settings(data_dir=tmp_path, auth_username=ADMIN["username"], auth_password=ADMIN["password"],
                               multi_user=True, country_db=False, **settings))


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


def test_reloading_the_admin_page_does_not_invite_again(setup, tmp_path):
    app, admin = setup
    res = admin.post("/admin/invitations", data={"email": "dani@example.com"}, follow_redirects=False)
    assert res.status_code == 303
    page = admin.get("/admin").text
    assert "/invite/" in page
    # A reload is a GET: no new invitation, and the link is not shown again
    assert "/invite/" not in admin.get("/admin").text
    with sqlite3.connect(tmp_path / "users.db") as db:
        assert db.execute("SELECT count(*) FROM invitations").fetchone() == (1,)
    assert "login-error" in admin.post("/admin/invitations", data={"email": "not-an-email"}).text


def test_a_new_invitation_for_an_email_replaces_the_open_one(setup, tmp_path):
    app, admin = setup
    old, new = invite(admin, "eva@example.com"), invite(admin, "eva@example.com")
    assert TestClient(app).get(f"/invite/{old}").status_code == 404
    assert TestClient(app).get(f"/invite/{new}").status_code == 200
    invite(admin)
    invite(admin)  # invitations for anyone are not replaced
    with sqlite3.connect(tmp_path / "users.db") as db:
        assert db.execute("SELECT count(*) FROM invitations").fetchone() == (3,)


def test_signing_up_in_a_logged_in_browser_says_it_and_logs_out(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    old_cookie = client.cookies[USER_COOKIE]
    token = invite(admin)
    page = client.get(f"/invite/{token}").text
    assert 'data-i18n="register.logged_in"' in page and "<strong>ana</strong>" in page
    assert 'data-i18n="register.logged_in"' not in TestClient(app).get(f"/invite/{token}").text
    client.post(f"/invite/{token}", data={"username": "bea", "email": "bea@example.com", "password": "bea-password",
                                          "password2": "bea-password"})
    assert client.get("/api/about").json()["user"] == "bea"
    # The old session is logged out for good, even if its cookie was kept
    other = TestClient(app)
    other.cookies.set(USER_COOKIE, old_cookie)
    assert other.get("/api/about").status_code == 401


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


def test_user_ids_are_never_reused(tmp_path):
    app = make_app(tmp_path)
    admin = admin_client(app, tmp_path)
    sign_up(app, invite(admin))
    with sqlite3.connect(tmp_path / "users.db") as db:
        assert "AUTOINCREMENT" in db.execute("SELECT sql FROM sqlite_master WHERE name = 'users'").fetchone()[0]


# --- Admin panel ---

def user_id(tmp_path, username):
    with sqlite3.connect(tmp_path / "users.db") as db:
        return db.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()[0]


def test_admin_sees_users_sizes_and_last_login(setup, tmp_path):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    TestClient(app, client=("203.0.113.9", 1)).post(
        "/login", data={"username": "ana", "password": "ana-password"},
        headers={"cf-connecting-ip": "203.0.113.9", "cf-ipcountry": "es"})
    page = admin.get("/admin").text
    assert "ana@example.com" in page and "203.0.113.9 · ES" in page
    assert re.search(r"admin.user_count\">Users</span>: <strong>1 / 100</strong>", page)
    assert " KB</td>" in page
    # The admin sees no data of the users
    ana.post("/api/accounts", json={"name": "Hidden", "type": "bank"})
    assert "Hidden" not in admin.get("/admin").text


def test_block_and_unblock_a_user(setup, tmp_path):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    ana.post("/api/accounts", json={"name": "Kept", "type": "bank"})
    uid = user_id(tmp_path, "ana")
    admin.post(f"/admin/users/{uid}/block")
    assert ana.get("/api/accounts").status_code == 401  # logged out at once
    res = TestClient(app).post("/login", data={"username": "ana", "password": "ana-password"}, follow_redirects=False)
    assert res.headers["location"] == "/login?error=1"
    assert "admin.status_blocked" in admin.get("/admin").text
    admin.post(f"/admin/users/{uid}/unblock")
    again = TestClient(app)
    again.post("/login", data={"username": "ana", "password": "ana-password"})
    assert [a["name"] for a in again.get("/api/accounts").json()] == ["Kept"]  # the data was kept


def test_delete_a_user_and_block_the_email(setup, tmp_path):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    bob, _ = sign_up(app, invite(admin), "bob", "bob@example.com", "bob-password")
    uid = user_id(tmp_path, "ana")
    admin.post(f"/admin/users/{uid}/delete?block_email=1")
    assert not (tmp_path / "users" / f"{uid}.db.enc").exists()
    assert ana.get("/api/accounts").status_code == 401
    assert bob.get("/api/accounts").status_code == 200  # nobody else is touched
    page = admin.get("/admin").text
    assert "ana@example.com" in page  # in the blocked emails
    _, res = sign_up(app, invite(admin), "ana2", "ana@example.com")
    assert "register.error.blocked" in res.text
    # A plain delete leaves the email free
    admin.post(f"/admin/users/{user_id(tmp_path, 'bob')}/delete")
    client, _ = sign_up(app, invite(admin), "bob", "bob@example.com", "bob-password")
    assert client.get("/api/accounts").json() == []  # a new, empty account


def test_blocked_emails_list(setup):
    app, admin = setup
    admin.post("/admin/blocked-emails", data={"email": "Spam@Example.com"})
    assert "spam@example.com" in admin.get("/admin").text
    _, res = sign_up(app, invite(admin), "spam", "spam@example.com")
    assert "register.error.blocked" in res.text
    admin.post("/admin/blocked-emails/unblock?email=spam%40example.com")
    client, _ = sign_up(app, invite(admin), "spam", "spam@example.com")
    assert USER_COOKIE in client.cookies
    assert "register.error.email" in admin.post("/admin/blocked-emails", data={"email": "nope"}).text


def test_admin_actions_need_the_admin(setup, tmp_path):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    uid = user_id(tmp_path, "ana")
    for path in (f"/admin/users/{uid}/delete", f"/admin/users/{uid}/block", "/admin/blocked-emails"):
        assert ana.post(path, data={"email": "x@example.com"}, follow_redirects=False).headers["location"] == "/login"
    assert (tmp_path / "users" / f"{uid}.db.enc").exists()


# --- Expiry, limits and the demo ---

def set_expiry(tmp_path, username, moment):
    with sqlite3.connect(tmp_path / "users.db") as db:
        db.execute("UPDATE users SET expires_at = ? WHERE username = ?",
                   (moment.isoformat(" ") if moment else None, username))


def test_new_accounts_last_30_days(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    about = client.get("/api/about").json()
    assert about["days_left"] in (29, 30) and about["expires"]


def test_expired_account_loses_its_data_and_is_blocked_until_extended(setup, tmp_path):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    ana.post("/api/accounts", json={"name": "Old", "type": "bank"})
    uid = user_id(tmp_path, "ana")
    set_expiry(tmp_path, "ana", now() - timedelta(minutes=1))
    assert ana.get("/api/accounts").status_code == 401  # logged out as soon as it expires
    assert app.state.users.purge_expired() == 1
    assert app.state.users.purge_expired() == 0  # once
    assert not (tmp_path / "users" / f"{uid}.db.enc").exists()
    assert "admin.status_expired" in admin.get("/admin").text
    login = {"username": "ana", "password": "ana-password"}
    assert TestClient(app).post("/login", data=login, follow_redirects=False).headers["location"] == "/login?error=1"

    admin.post(f"/admin/users/{uid}/extend", data={"days": "10"})
    again = TestClient(app)
    assert again.post("/login", data=login, follow_redirects=False).headers["location"] == "/"
    assert again.get("/api/accounts").json() == []  # a new, empty database
    assert again.get("/api/about").json()["days_left"] in (9, 10)


def test_extend_adds_days_and_no_expiry_removes_it(setup, tmp_path):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    uid = user_id(tmp_path, "ana")
    admin.post(f"/admin/users/{uid}/extend", data={"days": "5"})
    assert client.get("/api/about").json()["days_left"] in (34, 35)
    admin.post(f"/admin/users/{uid}/no-expiry")
    about = client.get("/api/about").json()
    assert about["expires"] is None and about["days_left"] is None
    assert "admin.never" in admin.get("/admin").text


def test_no_expiry_when_account_days_is_0(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, auth_username="admin", auth_password="admin-pass", multi_user=True,
                              country_db=False, account_days=0))
    client, _ = sign_up(app, invite(admin_client(app, tmp_path)))
    assert client.get("/api/about").json()["expires"] is None


def test_most_users(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, auth_username="admin", auth_password="admin-pass", multi_user=True,
                              country_db=False, max_users=1))
    admin = admin_client(app, tmp_path)
    sign_up(app, invite(admin))
    _, res = sign_up(app, invite(admin), "bob", "bob@example.com")
    assert "register.error.full" in res.text


def test_most_database_size(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    client.post("/api/accounts", json={"name": "Small", "type": "bank"})
    app.state.users.databases.max_bytes = len(b"x") * 60 * 1024  # a little over an empty database
    res = None
    for i in range(200):
        res = client.post("/api/accounts", json={"name": f"Big {i}", "type": "bank", "notes": "x" * 500})
        if res.status_code == 413:
            break
    assert res.status_code == 413 and "limit" in res.json()["detail"]
    names = [a["name"] for a in client.get("/api/accounts").json()]
    assert f"Big {i}" not in names and "Small" in names  # that change was not saved, the earlier ones were


def test_the_demo_without_an_account_is_one_per_visitor(setup):
    app, admin = setup
    one, two = TestClient(app), TestClient(app)
    assert one.get("/api/accounts").status_code == 401
    assert one.get("/demo?lang=es", follow_redirects=False).headers["location"] == "/"
    two.post("/api/demo?lang=en")
    first = one.get("/api/accounts").json()
    assert first and len(first) == len(two.get("/api/accounts").json())
    one.delete(f"/api/accounts/{first[0]['id']}")
    assert len(one.get("/api/accounts").json()) == len(first) - 1
    assert len(two.get("/api/accounts").json()) == len(first)  # the other visitor's demo is untouched
    assert one.get("/api/about").json()["demo"] is True
    # The demo gives no access to accounts or the admin
    assert one.get("/account", follow_redirects=False).headers["location"] == "/login"
    assert one.get("/admin", follow_redirects=False).headers["location"] == "/login"
    # Leaving it ends it
    one.delete("/api/demo")
    assert one.get("/api/accounts").status_code == 401
    # A made-up demo cookie gives nothing
    fake = TestClient(app)
    fake.cookies.set("abt_demo", "made-up")
    assert fake.get("/api/accounts").status_code == 401


def test_a_user_in_the_demo_does_not_touch_their_data(setup):
    app, admin = setup
    ana, _ = sign_up(app, invite(admin))
    ana.post("/api/accounts", json={"name": "Real", "type": "bank"})
    ana.post("/api/demo?lang=en")
    assert "Real" not in [a["name"] for a in ana.get("/api/accounts").json()]
    ana.delete("/api/demo")
    assert [a["name"] for a in ana.get("/api/accounts").json()] == ["Real"]


# --- Abuse: invitation race, guessing from many addresses, demo size, imports ---

def test_one_invitation_makes_one_account_even_when_sent_at_the_same_time(setup):
    import threading
    app, admin = setup
    token = invite(admin)
    users = app.state.users
    results = []

    def go(i):
        try:
            users.sign_up(token, f"user{i}", f"user{i}@example.com", "long-password")
            results.append("ok")
        except Exception as error:  # noqa: BLE001
            results.append(str(error))
    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert results.count("ok") == 1 and results.count("invitation") == 5
    assert len(users.list_users()) == 1


def test_wrong_passwords_for_one_user_are_limited_whatever_the_ip(setup):
    import app.main as main
    app, admin = setup
    sign_up(app, invite(admin))
    for i in range(main.WRONG_PASSWORDS_PER_USER):
        # A different IPv4 address each time: the per-IP limit never kicks in
        guesser = TestClient(app, client=(f"203.0.{i // 250}.{i % 250 + 1}", 50000))
        bad = guesser.post("/login", data={"username": "ana", "password": f"guess-{i:04d}"}, follow_redirects=False)
        assert bad.headers["location"] == "/login?error=1"
    blocked = TestClient(app, client=("198.51.100.9", 50000)).post(
        "/login", data={"username": "Ana", "password": "ana-password"}, follow_redirects=False)
    assert blocked.status_code == 429 and USER_COOKIE not in blocked.cookies
    # Other users are not affected
    sign_up(app, invite(admin), "bea", "bea@example.com", "bea-password")
    ok = TestClient(app, client=("198.51.100.9", 50000)).post(
        "/login", data={"username": "bea", "password": "bea-password"}, follow_redirects=False)
    assert ok.headers["location"] == "/"


def test_ipv6_addresses_of_one_64_count_as_one(setup):
    import app.main as main
    app, admin = setup
    sign_up(app, invite(admin))
    for i in range(main.WRONG_PASSWORDS_PER_IP):
        guesser = TestClient(app, client=(f"2001:db8:1:2::{i + 1:x}", 50000))
        guesser.post("/login", data={"username": f"nobody{i}", "password": "x" * 12})
    same_64 = TestClient(app, client=("2001:db8:1:2:ffff::1", 50000))
    assert same_64.post("/login", data={"username": "ana", "password": "ana-password"},
                        follow_redirects=False).status_code == 429
    other_64 = TestClient(app, client=("2001:db8:1:3::1", 50000))
    assert other_64.post("/login", data={"username": "ana", "password": "ana-password"},
                         follow_redirects=False).headers["location"] == "/"


def test_the_demo_has_a_size_limit(setup, monkeypatch):
    import app.demo as demo
    monkeypatch.setattr(demo, "MAX_BYTES", 300 * 1024)
    app, _ = setup
    visitor = TestClient(app)
    visitor.post("/api/demo")
    # Too big a file is refused before reading it all
    big = b"account,date,amount\n" + b"x,2020-01-01,1\n" * 80_000
    assert visitor.post("/api/import/csv", files={"file": ("b.csv", big, "text/csv")}).status_code == 413
    # Past the database's limit the change is refused with 413 and the demo still works
    rows = "\n".join(f"Account {i},2020-01-01,{i}" for i in range(5_000))
    res = visitor.post("/api/import/csv", files={"file": ("r.csv", f"account,date,amount\n{rows}".encode(), "text/csv")})
    assert res.status_code == 413 and "size limit" in res.json()["detail"]
    assert visitor.get("/api/accounts").status_code == 200


def test_password_checks_run_a_few_at_a_time(setup, monkeypatch):
    import asyncio
    import threading
    import app.main as main
    import httpx2 as httpx
    app, _ = setup
    running, most, lock = 0, 0, threading.Lock()

    def slow_login(*_args):
        nonlocal running, most
        with lock:
            running += 1
            most = max(most, running)
        time.sleep(0.1)
        with lock:
            running -= 1
    monkeypatch.setattr(app.state.users, "login", slow_login)

    async def run():
        async def one(i):
            transport = httpx.ASGITransport(app=app, client=(f"203.0.113.{i + 1}", 50000))
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                await client.post("/login", data={"username": f"user{i}", "password": "x" * 12})
        await asyncio.gather(*(one(i) for i in range(12)))
    asyncio.run(run())
    assert most == main.ARGON2_AT_ONCE


def test_a_csv_import_does_not_stop_the_app(setup, monkeypatch):
    import asyncio
    import app.main as main
    import httpx2 as httpx
    app, _ = setup

    def slow_import(*_args):
        time.sleep(0.5)
        return {"accounts_created": 0, "balances_created": 0, "balances_updated": 0, "errors": []}
    monkeypatch.setattr(main, "import_csv", slow_import)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            await client.post("/api/demo")
            stalls = []

            async def ticker():
                while True:
                    start = time.monotonic()
                    await asyncio.sleep(0.01)
                    stalls.append(time.monotonic() - start)
            tick = asyncio.create_task(ticker())
            res = await client.post("/api/import/csv", files={"file": ("a.csv", b"account,date,amount\n", "text/csv")})
            tick.cancel()
            return res.status_code, max(stalls)
    status, longest = asyncio.run(run())
    assert status == 200 and longest < 0.3


# --- A new data key on each password change or recovery ---

def data_key_of(app, client) -> bytes:
    return app.state.users.session(client.cookies[USER_COOKIE]).key


def opens(app, user_id: int, key: bytes) -> bool:
    path = app.state.users.databases.path(user_id)
    try:
        crypto.unseal(key, path.read_bytes(), db_aad(user_id))
        return True
    except crypto.WrongKey:
        return False


@pytest.mark.parametrize("how", ["password", "recovery"])
def test_a_new_password_re_encrypts_the_data_with_a_new_key(setup, how):
    app, admin = setup
    client, res = sign_up(app, invite(admin))
    key = recovery_key(res)
    client.post("/api/accounts", json={"name": "Kept", "type": "bank"})
    user_id = app.state.users.session(client.cookies[USER_COOKIE]).user_id
    # A backup (as made before a schema migration) is re-encrypted too
    path = app.state.users.databases.path(user_id)
    backup = path.with_name(path.name + ".backup-0005-to-0006-20260101-000000")
    backup.write_bytes(path.read_bytes())
    old = data_key_of(app, client)  # e.g. read from a stolen cookie

    if how == "password":
        client.post("/account/password", data={"current": "ana-password", "password": "new-password-1",
                                               "password2": "new-password-1"})
    else:
        client.post("/recover", data={"username": "ana", "recovery_key": key, "password": "new-password-1",
                                      "password2": "new-password-1"})
    new = data_key_of(app, client)
    assert new != old and opens(app, user_id, new) and not opens(app, user_id, old)
    assert crypto.unseal(new, backup.read_bytes(), db_aad(user_id))
    assert [a["name"] for a in client.get("/api/accounts").json()] == ["Kept"]
    # The recovery key saved at sign-up keeps working, and the data is still there
    other = TestClient(app)
    other.post("/recover", data={"username": "ana", "recovery_key": key, "password": "newer-password",
                                 "password2": "newer-password"})
    assert [a["name"] for a in other.get("/api/accounts").json()] == ["Kept"]


def test_accounts_from_before_data_keys_keep_working_and_move_on_password_change(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    client.post("/api/accounts", json={"name": "Kept", "type": "bank"})
    users = app.state.users
    # Turn it into an account of the previous version: no data key apart, the account key encrypts the database
    from app.users import User, data_key_aad, key_aad
    with users.Session() as db:
        user = db.scalar(__import__("sqlalchemy").select(User).where(User.username == "ana"))
        kek = crypto.password_key("ana-password", user.kdf_salt, json.loads(user.kdf_params))
        account_key = crypto.unseal(kek, user.key_by_password, key_aad(user.id))
        data_key = crypto.unseal(account_key, user.key_by_account, data_key_aad(user.id))
        path = users.databases.path(user.id)
        plain = crypto.unseal(data_key, path.read_bytes(), db_aad(user.id))
        path.write_bytes(crypto.seal(account_key, plain, db_aad(user.id)))
        user.key_by_account = None
        db.commit()
        user_id = user.id

    legacy = TestClient(app)
    legacy.post("/login", data={"username": "ana", "password": "ana-password"})
    assert [a["name"] for a in legacy.get("/api/accounts").json()] == ["Kept"]
    legacy.post("/account/password", data={"current": "ana-password", "password": "new-password-1",
                                           "password2": "new-password-1"})
    assert users.get_user(user_id).key_by_account is not None
    assert not opens(app, user_id, account_key)
    assert [a["name"] for a in legacy.get("/api/accounts").json()] == ["Kept"]


def test_a_re_encryption_cut_short_never_loses_the_data(setup):
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    client.post("/api/accounts", json={"name": "Kept", "type": "bank"})
    databases = app.state.users.databases
    user_id, old = app.state.users.session(client.cookies[USER_COOKIE]).user_id, data_key_of(app, client)
    new = crypto.new_key()
    pending = databases.path(user_id).with_name(f"{user_id}.db.enc.rekey")

    # The new key was never saved (users.db failed): the old file stays in use
    with pytest.raises(RuntimeError):
        with databases.rekey(user_id, old, new):
            raise RuntimeError("users.db could not be written")
    assert pending.exists()
    assert [a["name"] for a in client.get("/api/accounts").json()] == ["Kept"]
    assert not pending.exists()

    # The new key was saved but the app stopped before the new file replaced the old one: opening with it finishes
    pending.write_bytes(crypto.seal(new, crypto.unseal(old, databases.path(user_id).read_bytes(), db_aad(user_id)),
                                    db_aad(user_id)))
    with databases.open(user_id, new) as db:
        assert db.scalar(__import__("sqlalchemy").text("SELECT name FROM accounts")) == "Kept"
    assert not pending.exists() and opens(app, user_id, new)


def test_users_db_from_before_gets_the_new_column_after_a_backup(tmp_path):
    import sqlite3
    app = make_app(tmp_path)
    admin = admin_client(app, tmp_path)
    sign_up(app, invite(admin))
    with sqlite3.connect(tmp_path / "users.db") as connection:
        connection.execute("ALTER TABLE users DROP COLUMN key_by_account")
    connection.close()
    make_app(tmp_path)  # starts again: adds the column
    with sqlite3.connect(tmp_path / "users.db") as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(users)")]
        names = [row[0] for row in connection.execute("SELECT username FROM users")]
    connection.close()
    assert "key_by_account" in columns and names == ["ana"]
    backups = list(tmp_path.glob("users.db.backup-*"))
    assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600


# --- Demos anyone can start: limits ---

def test_demos_started_from_one_address_are_limited(setup):
    import app.main as main
    app, _ = setup
    for _ in range(main.DEMOS_PER_IP):
        assert TestClient(app, client=("203.0.113.5", 1)).post("/api/demo").status_code == 204
    blocked = TestClient(app, client=("203.0.113.5", 1)).get("/demo", follow_redirects=False)
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0
    # The same visitor keeps their demo; other addresses can still start one
    assert TestClient(app, client=("198.51.100.5", 1)).post("/api/demo").status_code == 204


def rates_app(tmp_path, rates, **extra):
    return create_app(Settings(data_dir=tmp_path, auth_username=ADMIN["username"], auth_password=ADMIN["password"],
                               multi_user=True, country_db=False, **extra), fx_fetcher=rates)


def usd_account(client):
    account = client.post("/api/accounts", json={"name": "Dollars", "currency": "USD"}).json()
    for day in ("2026-01-01", "2026-02-01", "2026-03-01"):
        client.post(f"/api/accounts/{account['id']}/balances", json={"date": day, "amount": 100})
    client.post("/api/accounts", json={"name": "Euros"})


def test_users_share_the_exchange_rates(tmp_path):
    from tests.test_fx import FakeRates
    rates = FakeRates()
    app = rates_app(tmp_path, rates)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    bea, _ = sign_up(app, invite(admin), "bea", "bea@example.com", "bea-password")
    usd_account(ana)
    assert ana.get("/api/history/converted").status_code == 200
    assert ana.get("/api/summary/combined").json()["missing"] == []
    asked = len(rates.calls)
    assert asked == 1  # today's rate, for the chart and the total
    # Another user with the same currencies and dates: nothing asked again
    usd_account(bea)
    assert bea.get("/api/history/converted").status_code == 200
    assert bea.get("/api/summary/combined").json()["missing"] == []
    assert len(rates.calls) == asked
    assert all(isinstance(call[0], str) and len(call) == 3 for call in rates.calls)  # currencies and a date only


def test_requests_to_the_rate_service_have_a_daily_limit(tmp_path):
    from tests.test_fx import FakeRates
    rates = FakeRates()
    app = rates_app(tmp_path, rates, fx_daily_limit=3)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    start = date(2020, 1, 1)
    for n in range(5):
        ana.get(f"/api/fx?from=USD&to=EUR&date={start + timedelta(days=n)}")
    assert len(rates.calls) == 3
    # The count is kept across restarts
    again = rates_app(tmp_path, rates, fx_daily_limit=3)
    client = TestClient(again)
    client.post("/login", data={"username": "ana", "password": "ana-password"})
    assert client.get(f"/api/fx?from=USD&to=EUR&date={start + timedelta(days=10)}").status_code == 200
    assert len(rates.calls) == 3


def test_demos_never_ask_the_rate_service(tmp_path):
    from tests.test_fx import FakeRates
    rates = FakeRates()
    app = rates_app(tmp_path, rates)
    visitor = TestClient(app)
    visitor.post("/api/demo")
    assert visitor.get("/api/history/converted").status_code == 200
    assert visitor.get("/api/summary/combined").status_code == 200
    visitor.get("/api/fx?from=USD&to=EUR&date=2020-01-01")
    visitor.post("/api/fx/refresh")
    assert rates.calls == []


def test_todays_rate_is_asked_again_every_3_hours_until_the_days_own_rate(tmp_path):
    from app.fx import SharedRates
    from tests.test_fx import FakeRates
    now = [1_000_000.0]
    today = date.today()
    rates = FakeRates()
    rates_by_day = {}
    rates.__class__ = type("Rates", (FakeRates,), {"__call__": lambda self, b, q, d: (
        self.calls.append((b, q, d)) or (self.rate, rates_by_day.get("date", today - timedelta(days=1))))})
    store = SharedRates(tmp_path / "rates.db", rates, 90, clock=lambda: now[0])
    fetch = store.fetcher()
    fetch("USD", "EUR", None)
    fetch("USD", "EUR", None)
    assert len(rates.calls) == 1  # yesterday's rate, asked less than 3 hours ago
    now[0] += 2 * 3600
    fetch("USD", "EUR", None)
    assert len(rates.calls) == 1
    now[0] += 3601
    rates_by_day["date"] = today  # the ECB has published today's
    fetch("USD", "EUR", None)
    assert len(rates.calls) == 2
    now[0] += 5 * 3600
    fetch("USD", "EUR", None)
    assert len(rates.calls) == 2  # today's own rate: not asked again today


def test_the_api_docs_need_an_account_also_in_the_demo(tmp_path):
    app = make_app(tmp_path, api_docs=True)
    admin = admin_client(app, tmp_path)
    visitor = TestClient(app)
    visitor.post("/api/demo")
    assert visitor.get("/api/accounts").status_code == 200  # the demo itself works
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        res = visitor.get(path, follow_redirects=False)
        assert res.status_code == 303 and res.headers["location"] == "/login"
    assert visitor.get("/openapi.json", follow_redirects=False).status_code == 303
    user, _ = sign_up(app, invite(admin))
    assert user.get("/docs").status_code == 200 and "paths" in user.get("/openapi.json").json()
