"""What's new (#205): CHANGELOG.md read into releases, and per user the last version seen and the 'show it' choice."""
from pathlib import Path

from fastapi.testclient import TestClient

from app.changelog import CHANGELOG, parse, releases
from app.config import Settings
from app.main import create_app
from tests.test_multi_user import admin_client, fast_argon2, invite, make_app, sign_up  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """# Changelog

## [0.50.0](https://github.com/o/r/compare/v0.49.11...v0.50.0) (2026-11-23)


### Features

* show what changed in each version ([1234567](https://github.com/o/r/commit/1234567abc)), closes [#205](https://github.com/o/r/issues/205)
* show what changed in each version ([89abcde](https://github.com/o/r/commit/89abcdef0)), closes [#205](https://github.com/o/r/issues/205) [#206](https://github.com/o/r/issues/206)
* **charts:** a `faster` [legend](https://example.com) ([fedcba9](https://github.com/o/r/commit/fedcba9))


### Bug fixes

* never reuse a user id ([4001e05](https://github.com/o/r/commit/4001e05)), closes [#176](https://github.com/o/r/issues/176)

## 0.49.11 (2026-11-22)

## [0.49.10](https://github.com/o/r/compare/v0.49.9...v0.49.10) (2026-11-21)

### Bug fixes

* **deps:** bump the python-packages group with 8 updates ([280470e](https://github.com/o/r/commit/280470e))
"""


def test_parse_reads_releases_sections_and_items():
    """Versions, dates, sections and items come out newest first, without commit links or 'closes' tails."""
    parsed = parse(SAMPLE)
    assert [(r["version"], r["date"]) for r in parsed] == [
        ("0.50.0", "2026-11-23"), ("0.49.11", "2026-11-22"), ("0.49.10", "2026-11-21")]
    features, fixes = parsed[0]["sections"]
    assert features["title"] == "Features" and fixes["title"] == "Bug fixes"
    assert features["items"] == [
        {"scope": None, "text": "show what changed in each version", "issues": [205, 206]},  # the repeat is merged
        {"scope": "charts", "text": "a faster legend", "issues": []},
    ]
    assert fixes["items"] == [{"scope": None, "text": "never reuse a user id", "issues": [176]}]
    assert parsed[1]["sections"] == []
    assert parsed[2]["sections"][0]["items"][0]["scope"] == "deps"


def test_parse_ignores_what_is_not_a_release():
    """Text before the first release, or items outside a section, are left out; an empty file has no releases."""
    assert parse("") == []
    assert parse("# Changelog\n\n* loose\n\n## 1.0.0 (2026-01-01)\n\n* no section\n") == [
        {"version": "1.0.0", "date": "2026-01-01", "sections": []}]


def test_missing_changelog_means_no_releases(tmp_path):
    """Without CHANGELOG.md (e.g. an image built without it) the list is empty, not an error."""
    assert releases(tmp_path / "CHANGELOG.md") == ()


def test_the_repository_changelog_is_served(client):
    """/api/changelog gives the app's version and the releases of the repository's CHANGELOG.md."""
    data = client.get("/api/changelog").json()
    assert data["releases"] == list(releases())
    assert data["releases"] and data["releases"][0]["version"]
    assert set(data) == {"version", "releases"}


def test_the_image_includes_the_changelog():
    """The Docker build copies CHANGELOG.md next to the app (and .dockerignore lets it through)."""
    assert "COPY CHANGELOG.md ./CHANGELOG.md" in (ROOT / "Dockerfile").read_text()
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    assert lines.index("!CHANGELOG.md") > lines.index("*.md")
    assert CHANGELOG == ROOT / "CHANGELOG.md"


def test_changelog_state_starts_empty_and_is_stored(client, tmp_path):
    """No version seen and 'show it' on at first; both are kept, also after a restart, and can change separately."""
    assert client.get("/api/changelog/state").json() == {"seen": None, "auto": True}
    assert client.put("/api/changelog/state", json={"seen": "0.49.11"}).json() == {"seen": "0.49.11", "auto": True}
    assert client.put("/api/changelog/state", json={"auto": False}).json() == {"seen": "0.49.11", "auto": False}
    restarted = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR")))
    assert restarted.get("/api/changelog/state").json() == {"seen": "0.49.11", "auto": False}
    assert restarted.put("/api/changelog/state", json={"seen": "0.50.0", "auto": True}).json() == {
        "seen": "0.50.0", "auto": True}


def test_changelog_state_rejects_bad_values(client):
    """A seen value that is not X.Y.Z, or an 'auto' that is not a boolean, is refused and nothing is stored."""
    for body in ({"seen": "dev"}, {"seen": "1.2"}, {"seen": "1.2.3; drop"}, {"seen": 3}, {"auto": "maybe"}):
        assert client.put("/api/changelog/state", json=body).status_code == 422, body
    assert client.get("/api/changelog/state").json() == {"seen": None, "auto": True}


def test_changelog_state_is_per_user(tmp_path):
    """With several users, each one has their own version seen and choice (in their encrypted database)."""
    app = make_app(tmp_path)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    bob, _ = sign_up(app, invite(admin), username="bob", email="bob@example.com", password="bob-password")
    assert ana.put("/api/changelog/state", json={"seen": "1.0.0", "auto": False}).status_code == 200
    assert ana.get("/api/changelog/state").json() == {"seen": "1.0.0", "auto": False}
    assert bob.get("/api/changelog/state").json() == {"seen": None, "auto": True}


def test_changelog_state_in_the_demo_stays_in_the_demo(client):
    """In the demo, the state goes to the demo's own database, not the real one."""
    client.put("/api/changelog/state", json={"seen": "1.0.0"})
    assert client.post("/api/demo").status_code in (200, 201, 204)
    assert client.get("/api/changelog/state").json() == {"seen": None, "auto": True}
    client.put("/api/changelog/state", json={"seen": "2.0.0", "auto": False})
    assert client.get("/api/changelog").json()["releases"]  # the changelog itself is there in the demo too
    client.delete("/api/demo")
    assert client.get("/api/changelog/state").json() == {"seen": "1.0.0", "auto": True}
