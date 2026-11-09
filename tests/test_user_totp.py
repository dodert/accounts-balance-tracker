"""Multi-user mode: each user's optional one-time code (TOTP)."""
import re
import time

import pytest
from fastapi.testclient import TestClient

from app.totp import code_at
from app.users import USER_COOKIE, USER_PENDING_COOKIE
from tests.test_multi_user import admin_client, fast_argon2, invite, make_app, recovery_key, sign_up  # noqa: F401

PASSWORD = "ana-password"


@pytest.fixture
def setup(tmp_path):
    app = make_app(tmp_path)
    return app, admin_client(app, tmp_path)


def step() -> int:
    return int(time.time() // 30)


def turn_on(client, at: int):
    """Turns the code on from the account page with the code of step `at`; returns the secret."""
    page = client.get("/account").text
    secret = re.search(r'name="secret" value="([A-Z2-7]+)"', page).group(1)
    res = client.post("/account/totp", data={"secret": secret, "code": code_at(secret, at), "current": PASSWORD},
                      follow_redirects=False)
    assert res.headers["location"] == "/account?totp=on"
    return secret


def log_in(app):
    client = TestClient(app)
    res = client.post("/login", data={"username": "ana", "password": PASSWORD}, follow_redirects=False)
    return client, res


def test_turning_the_code_on_needs_a_right_code_and_password(setup):
    """The code is turned on only with a right first code and the user's password."""
    app, admin = setup
    client, _ = sign_up(app, invite(admin))
    page = client.get("/account").text
    assert 'action="/account/totp"' in page and "<svg" in page  # off: the QR code of a new secret
    secret = re.search(r'name="secret" value="([A-Z2-7]+)"', page).group(1)
    wrong_code = client.post("/account/totp", data={"secret": secret, "code": "000000", "current": PASSWORD})
    assert "totp.error" in wrong_code.text
    wrong_password = client.post("/account/totp", data={"secret": secret, "code": code_at(secret, step()),
                                                        "current": "not-the-password"})
    assert "account.error" in wrong_password.text
    assert not app.state.users.totp_on(1)
    turn_on(client, step())
    assert app.state.users.totp_on(1) and 'action="/account/totp/off"' in client.get("/account").text
    assert secret not in app.state.users.get_user(1).totp_secret  # stored sealed, not as typed


def test_login_asks_for_the_code_and_each_code_works_once(setup):
    """With the code on, the password alone gives no session; the right code does, once."""
    app, admin = setup
    owner, _ = sign_up(app, invite(admin))
    secret = turn_on(owner, step() - 1)
    client, res = log_in(app)
    assert res.headers["location"] == "/login/totp"
    assert client.cookies.get(USER_COOKIE) is None
    assert client.get("/api/accounts").status_code == 401  # not logged in yet
    assert 'data-mode="verify"' in client.get("/login/totp").text
    assert client.post("/login/totp", data={"code": "123456"}, follow_redirects=False).headers["location"] \
        == "/login/totp?error=1"
    code = code_at(secret, step())
    assert client.post("/login/totp", data={"code": code}, follow_redirects=False).headers["location"] == "/"
    assert client.get("/api/accounts").status_code == 200
    again, _ = log_in(app)
    assert again.post("/login/totp", data={"code": code}, follow_redirects=False).headers["location"] \
        == "/login/totp?error=1"  # already used


def test_the_pending_cookie_is_not_a_session(setup):
    """The cookie that waits for the code doesn't work as a session cookie."""
    app, admin = setup
    owner, _ = sign_up(app, invite(admin))
    turn_on(owner, step())
    client, _ = log_in(app)
    pending = client.cookies.get(USER_PENDING_COOKIE)
    assert pending
    thief = TestClient(app)
    thief.cookies.set(USER_COOKIE, pending)
    assert thief.get("/api/accounts").status_code == 401


def test_turning_the_code_off_needs_the_password(setup):
    """The user turns the code off with their password; then the password alone logs in."""
    app, admin = setup
    owner, _ = sign_up(app, invite(admin))
    turn_on(owner, step())
    assert "account.error" in owner.post("/account/totp/off", data={"current": "wrong-password"}).text
    assert app.state.users.totp_on(1)
    res = owner.post("/account/totp/off", data={"current": PASSWORD}, follow_redirects=False)
    assert res.headers["location"] == "/account?totp=off" and not app.state.users.totp_on(1)
    assert log_in(app)[1].headers["location"] == "/"


def test_the_admin_can_remove_a_users_code(setup):
    """The admin removes the code of a user who lost their app."""
    app, admin = setup
    owner, _ = sign_up(app, invite(admin))
    turn_on(owner, step())
    assert "admin.remove_totp" in admin.get("/admin").text
    admin.post("/admin/users/1/totp/remove")
    assert not app.state.users.totp_on(1) and "admin.remove_totp" not in admin.get("/admin").text
    assert log_in(app)[1].headers["location"] == "/"


def test_the_recovery_key_logs_in_without_the_code(setup):
    """The recovery key sets a new password and logs in without the code, which stays on."""
    app, admin = setup
    owner, res = sign_up(app, invite(admin))
    key = recovery_key(res)
    turn_on(owner, step())
    other = TestClient(app)
    ok = other.post("/recover", data={"username": "ana", "recovery_key": key, "password": "new-password-1",
                                      "password2": "new-password-1"}, follow_redirects=False)
    assert ok.headers["location"] == "/" and other.get("/api/accounts").status_code == 200
    assert app.state.users.totp_on(1)
