"""Request bodies over the limit are refused before the app stores them (#163)."""
from fastapi.testclient import TestClient

from app import middleware
from app.routes.transfer import MAX_DEMO_CSV_BYTES

from .test_multi_user import fast_argon2, make_app  # noqa: F401  (fixture used by the multi-user tests)


def csv_of(size: int) -> bytes:
    rows = b"account,date,amount\n"
    return rows + b"Savings,2020-01-01,1\n" * ((size - len(rows)) // 21 + 1)


def test_big_json_body_is_refused(client):
    """A JSON body over the limit gets 413 and changes nothing."""
    res = client.post("/api/accounts", json={"name": "x", "notes": "a" * (middleware.MAX_BODY + 1)})
    assert res.status_code == 413 and "too large" in res.json()["detail"]
    assert res.headers["X-Content-Type-Options"] == "nosniff"  # still inside the security headers
    assert client.get("/api/accounts").json() == []


def test_body_sent_in_chunks_is_cut_off(client):
    """A body without Content-Length is refused as soon as it passes the limit."""
    def chunks():
        for _ in range(100):
            yield b"x" * 64 * 1024
    res = client.post("/api/accounts", content=chunks(), headers={"Content-Type": "application/json"})
    assert "content-length" not in res.request.headers
    assert res.status_code == 413 and client.get("/api/accounts").json() == []


def test_public_login_form_with_a_big_file_is_refused(tmp_path):
    """Without a session, a multipart login with a big file part is refused before it is stored."""
    client = TestClient(make_app(tmp_path))
    res = client.post("/login", data={"username": "a", "password": "b"},
                      files={"junk": ("x", b"0" * (middleware.MAX_BODY + 1))}, follow_redirects=False)
    assert res.status_code == 413


def test_imports_take_bigger_files(client):
    """The imports accept files bigger than the general limit."""
    res = client.post("/api/import/csv", files={"file": ("b.csv", csv_of(2 * middleware.MAX_BODY), "text/csv")})
    assert res.status_code == 200


def test_demo_import_limit_applies_before_reading(tmp_path):
    """A demo visitor's import over the demo's limit is refused before it is stored."""
    visitor = TestClient(make_app(tmp_path))
    visitor.post("/api/demo")
    big = csv_of(MAX_DEMO_CSV_BYTES + middleware.FORM_OVERHEAD)
    res = visitor.post("/api/import/csv", files={"file": ("b.csv", big, "text/csv")})
    assert res.status_code == 413 and "too large" in res.json()["detail"]
