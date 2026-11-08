import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(tmp_path, **auth):
    return TestClient(create_app(Settings(data_dir=tmp_path, **auth)))


def test_no_login_when_not_configured(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/accounts").status_code == 200
    assert client.get("/api/about").json()["auth"] is False
    assert client.get("/login", follow_redirects=False).headers["location"] == "/"


def test_only_one_variable_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="AUTH_USERNAME and AUTH_PASSWORD"):
        make_client(tmp_path, auth_username="admin")


def test_login_required(tmp_path):
    client = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    assert client.get("/api/accounts").status_code == 401
    assert client.get("/api/export/csv", follow_redirects=False).status_code == 401
    page = client.get("/", follow_redirects=False)
    assert page.status_code == 303 and page.headers["location"] == "/login"
    # The login page, its assets and the Docker health check stay reachable
    assert 'action="/login"' in client.get("/login").text
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/i18n/es.json").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_wrong_password_then_login_and_logout(tmp_path):
    client = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    bad = client.post("/login", data={"username": "admin", "password": "nope"}, follow_redirects=False)
    assert bad.headers["location"] == "/login?error=1" and "abt_session" not in bad.cookies
    ok = client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    assert ok.headers["location"] == "/" and "httponly" in ok.headers["set-cookie"].lower()
    assert client.get("/api/accounts").status_code == 200
    assert client.get("/api/about").json()["auth"] is True
    cookie = client.cookies["abt_session"]
    assert client.get("/logout").status_code == 405  # a link or image elsewhere can't log you out
    assert client.get("/api/accounts").status_code == 200
    client.post("/logout")
    assert client.get("/api/accounts").status_code == 401
    # A copy of the cookie stops working too, also after a restart
    client.cookies.set("abt_session", cookie)
    assert client.get("/api/accounts").status_code == 401
    restarted = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    restarted.cookies.set("abt_session", cookie)
    assert restarted.get("/api/accounts").status_code == 401


def test_logout_keeps_other_devices_logged_in(tmp_path):
    phone = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    pc = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    phone.post("/login", data={"username": "admin", "password": "s3cret"})
    pc.post("/login", data={"username": "admin", "password": "s3cret"})
    phone.post("/logout")
    assert phone.get("/api/accounts").status_code == 401
    assert pc.get("/api/accounts").status_code == 200


def test_session_survives_restart_but_not_password_change(tmp_path):
    first = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    first.post("/login", data={"username": "admin", "password": "s3cret"})
    cookie = first.cookies["abt_session"]
    same = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    same.cookies.set("abt_session", cookie)
    assert same.get("/api/accounts").status_code == 200
    changed = make_client(tmp_path, auth_username="admin", auth_password="other")
    changed.cookies.set("abt_session", cookie)
    assert changed.get("/api/accounts").status_code == 401
    forged = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    forged.cookies.set("abt_session", "9999999999.00.deadbeef")
    assert forged.get("/api/accounts").status_code == 401


def test_forged_cookie_with_huge_expiry_is_rejected_not_a_crash(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path, auth_username="admin", auth_password="pw")))
    for forged in ("9" * 5000).encode() + b".00.abc", b"9999999999.00.d\xe9adbeef":
        assert client.get("/api/accounts", headers={"cookie": b"abt_session=" + forged}).status_code == 401


def totp_client(tmp_path):
    return make_client(tmp_path, auth_username="admin", auth_password="s3cret", auth_totp=True)


def current_code(tmp_path, offset=0):
    import json
    import time

    from app.totp import code_at
    secret = json.loads((tmp_path / ".totp_secret").read_text())["secret"]
    return code_at(secret, int(time.time() // 30) + offset)


def test_totp_setup_then_code_required(tmp_path):
    client = totp_client(tmp_path)
    # Without the password first, the code page sends back to the login
    assert client.get("/login/totp", follow_redirects=False).headers["location"] == "/login"
    res = client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    assert res.headers["location"] == "/login/totp" and "abt_session" not in res.cookies
    assert client.get("/api/accounts").status_code == 401  # password alone isn't enough

    page = client.get("/login/totp").text
    assert "<svg" in page and 'data-mode="setup"' in page
    bad = client.post("/login/totp", data={"code": "000000"}, follow_redirects=False)
    assert bad.headers["location"] == "/login/totp?error=1"
    ok = client.post("/login/totp", data={"code": current_code(tmp_path)}, follow_redirects=False)
    assert ok.headers["location"] == "/" and client.get("/api/accounts").status_code == 200

    # Registered: the secret is no longer shown, only the code is asked
    client.post("/logout")
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    page = client.get("/login/totp").text
    assert "<svg" not in page and 'data-mode="verify"' in page
    # The code already used can't be used again
    reused = client.post("/login/totp", data={"code": current_code(tmp_path)}, follow_redirects=False)
    assert reused.headers["location"] == "/login/totp?error=1"
    later = client.post("/login/totp", data={"code": current_code(tmp_path, 1)}, follow_redirects=False)
    assert later.headers["location"] == "/"


def test_totp_needs_login(tmp_path):
    with pytest.raises(ValueError, match="AUTH_TOTP"):
        make_client(tmp_path, auth_totp=True)


def test_totp_secret_survives_restart(tmp_path):
    totp_client(tmp_path)
    secret = (tmp_path / ".totp_secret").read_text()
    totp_client(tmp_path)
    assert (tmp_path / ".totp_secret").read_text() == secret


def test_pending_cookie_cannot_be_turned_into_a_session(tmp_path):
    client = totp_client(tmp_path)
    client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    expires, signature = client.cookies["abt_pending"].split(".")
    client.cookies.clear()
    client.cookies.set("abt_session", f"{expires}.pending.{signature}")
    assert client.get("/api/accounts").status_code == 401


def test_damaged_totp_secret_stops_the_app_instead_of_showing_a_new_one(tmp_path):
    totp_client(tmp_path)
    (tmp_path / ".totp_secret").write_text("{not json")
    with pytest.raises(ValueError, match="damaged"):
        totp_client(tmp_path)


def guess_in_parallel(app, path, field_values, extra=None):
    """Post every value at the same time, as an attacker would; returns (value, status, location)."""
    import asyncio
    import httpx2 as httpx

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                                     cookies=extra or {}) as client:
            async def one(field, value):
                res = await client.post(path, data=field, follow_redirects=False)
                return value, res.status_code, res.headers.get("location")
            return await asyncio.gather(*(one(field, value) for field, value in field_values))
    return asyncio.run(run())


def test_parallel_password_guesses_from_one_ip_are_checked_one_at_a_time(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0.05)
    app = create_app(Settings(data_dir=tmp_path, auth_username="admin", auth_password="s3cret"))
    wrong = [({"username": "admin", "password": f"guess{i}"}, f"guess{i}") for i in range(14)]
    results = guess_in_parallel(app, "/login", wrong + [({"username": "admin", "password": "s3cret"}, "s3cret")])
    # While one try is being checked (and waits its delay), the others from the same IP are turned away
    checked = [r for r in results if r[1] == 303]
    assert len(checked) == 1 and checked[0][2] == "/login?error=1"
    assert dict((r[0], r[1]) for r in results)["s3cret"] == 429


def test_too_many_wrong_passwords_lock_out_that_ip_only(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0)
    app = create_app(Settings(data_dir=tmp_path, auth_username="admin", auth_password="s3cret"))
    attacker = TestClient(app, client=("203.0.113.7", 50000))
    for i in range(main.WRONG_PASSWORDS_PER_IP):
        bad = attacker.post("/login", data={"username": "admin", "password": f"guess{i}"}, follow_redirects=False)
        assert bad.headers["location"] == "/login?error=1"
    # Now even the right password is not checked from that IP
    blocked = attacker.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    assert blocked.status_code == 429 and "abt_session" not in blocked.cookies
    assert 0 < int(blocked.headers["Retry-After"]) <= main.LOGIN_WINDOW
    # Everyone else can still log in
    owner = TestClient(app, client=("198.51.100.2", 50000))
    ok = owner.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    assert ok.headers["location"] == "/" and "abt_session" in ok.cookies


def test_a_right_password_forgets_the_wrong_ones_of_that_ip(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0)
    client = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    for _ in range(2):
        for i in range(main.WRONG_PASSWORDS_PER_IP - 1):
            client.post("/login", data={"username": "admin", "password": f"typo{i}"})
        ok = client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
        assert ok.headers["location"] == "/"


def test_parallel_code_guesses_from_one_ip_are_checked_one_at_a_time(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0.05)
    client = totp_client(tmp_path)
    client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    pending = {"abt_pending": client.cookies["abt_pending"]}
    guesses = [({"code": f"{i:06d}"}, i) for i in range(20)]
    results = guess_in_parallel(client.app, "/login/totp", guesses, pending)
    assert sum(r[1] == 429 for r in results) == 19


def test_too_many_wrong_codes_lock_out_that_ip(tmp_path, monkeypatch):
    import app.main as main
    monkeypatch.setattr(main, "WRONG_PASSWORD_DELAY", 0)
    client = totp_client(tmp_path)
    client.post("/login", data={"username": "admin", "password": "s3cret"}, follow_redirects=False)
    for i in range(main.WRONG_CODES_PER_IP):
        client.post("/login/totp", data={"code": f"{i:06d}"}, follow_redirects=False)
    blocked = client.post("/login/totp", data={"code": current_code(tmp_path)}, follow_redirects=False)
    assert blocked.status_code == 429 and "abt_session" not in blocked.cookies


def test_security_headers(tmp_path):
    client = make_client(tmp_path, auth_username="admin", auth_password="s3cret")
    for res in (client.get("/login"), client.get("/api/accounts"), client.get("/static/style.css")):
        assert "frame-ancestors 'none'" in res.headers["content-security-policy"]
        assert "script-src 'self';" in res.headers["content-security-policy"]
        assert res.headers["x-frame-options"] == "DENY"
        assert res.headers["x-content-type-options"] == "nosniff"
        assert res.headers["referrer-policy"] == "same-origin"
        assert "strict-transport-security" not in res.headers
    https = client.get("/login", headers={"x-forwarded-proto": "https"})
    assert https.headers["strict-transport-security"] == "max-age=31536000"
    # No inline scripts left for the policy to block
    assert "<script>" not in client.get("/login").text
    # FastAPI's interactive docs need their CDN and inline script
    client.post("/login", data={"username": "admin", "password": "s3cret"})
    assert "https://cdn.jsdelivr.net" in client.get("/docs").headers["content-security-policy"]
