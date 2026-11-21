"""Guided tour (#156): the version seen is kept in the user's own database."""
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_multi_user import admin_client, fast_argon2, invite, make_app, sign_up  # noqa: F401 (fixture)


def test_tour_seen_starts_at_zero_and_is_stored(client, tmp_path):
    """A new database has not seen the tour; marking it keeps the version, also after a restart."""
    assert client.get("/api/tour").json() == {"seen": 0}
    assert client.put("/api/tour", json={"seen": 1}).json() == {"seen": 1}
    assert client.put("/api/tour", json={"seen": 2}).json() == {"seen": 2}
    restarted = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR")))
    assert restarted.get("/api/tour").json() == {"seen": 2}


def test_tour_seen_rejects_bad_values(client):
    """A negative, too big or missing version is refused and nothing is stored."""
    for body in ({"seen": -1}, {"seen": 1001}, {}, {"seen": "x"}):
        assert client.put("/api/tour", json=body).status_code == 422
    assert client.get("/api/tour").json() == {"seen": 0}


def test_tour_seen_is_per_user(tmp_path):
    """With several users, each one has their own 'seen' (in their encrypted database)."""
    app = make_app(tmp_path)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    bob, _ = sign_up(app, invite(admin), username="bob", email="bob@example.com", password="bob-password")
    assert ana.put("/api/tour", json={"seen": 1}).status_code == 200
    assert ana.get("/api/tour").json() == {"seen": 1}
    assert bob.get("/api/tour").json() == {"seen": 0}


def test_tour_seen_in_the_demo_stays_in_the_demo(client):
    """In the demo, 'seen' goes to the demo's own database, not the real one."""
    assert client.post("/api/demo").status_code in (200, 201, 204)
    assert client.get("/api/tour").json() == {"seen": 0}
    client.put("/api/tour", json={"seen": 1})
    assert client.get("/api/tour").json() == {"seen": 1}
    client.delete("/api/demo")
    assert client.get("/api/tour").json() == {"seen": 0}


def test_driver_js_is_served_by_the_app_unchanged(client):
    """driver.js is served from /static (no CDN), with the files whose hashes its README records."""
    import hashlib
    from pathlib import Path
    vendor = Path(__file__).parent.parent / "app" / "static" / "vendor" / "driver"
    recorded = dict(line.split()[::-1] for line in (vendor / "README.md").read_text().splitlines()
                    if len(line.split()) == 2 and len(line.split()[0]) == 64)
    for name in ("driver.min.js", "driver.css"):
        res = client.get(f"/static/vendor/driver/{name}")
        assert res.status_code == 200
        assert hashlib.sha256(res.content).hexdigest() == recorded[name], name
    assert client.get("/static/vendor/driver/driver.min.js").headers["content-type"].startswith("text/javascript")
    assert "http" not in (Path(__file__).parent.parent / "app" / "static" / "js" / "tour.js").read_text()
