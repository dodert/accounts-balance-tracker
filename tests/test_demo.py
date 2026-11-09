from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fastapi.testclient import TestClient

from app.config import Settings
from app.demo import COOKIE, SAMPLES, month_starts
from app.main import create_app


def test_month_starts():
    """The demo's month helper gives the first day of the last months."""
    assert month_starts(date(2026, 2, 15), 3) == [date(2025, 12, 1), date(2026, 1, 1), date(2026, 2, 1)]


def test_demo_never_touches_real_data(client):
    """The demo uses its own sample data and never reads or changes the real data."""
    real = client.post("/api/accounts", json={"name": "My real account", "currency": "EUR"}).json()
    client.post(f"/api/accounts/{real['id']}/balances", json={"date": "2026-01-01", "amount": 123456})
    assert client.get("/api/about").json()["demo"] is False

    assert client.post("/api/demo?lang=es").status_code == 204
    assert client.cookies.get(COOKIE) == "1"
    assert client.get("/api/about").json()["demo"] is True
    demo_accounts = client.get("/api/accounts").json()
    assert len(demo_accounts) == len(SAMPLES)
    assert "My real account" not in {a["name"] for a in demo_accounts}
    assert "1234.56" not in client.get("/api/export/csv").text

    # Changes in demo mode stay in the demo database
    client.delete(f"/api/accounts/{demo_accounts[0]['id']}")
    client.post("/api/accounts", json={"name": "Made in the demo", "currency": "EUR"})

    assert client.delete("/api/demo").status_code == 204
    assert client.get("/api/about").json()["demo"] is False
    accounts = client.get("/api/accounts").json()
    assert [a["name"] for a in accounts] == ["My real account"]
    assert accounts[0]["balance"] == 123456


def test_demo_covers_the_features(client):
    """The demo data shows every feature (currencies, locked money, loans, archived...)."""
    client.post("/api/demo?lang=en")
    accounts = client.get("/api/accounts").json()
    by_institution = {}
    for a in accounts:
        by_institution.setdefault(a["institution"], []).append(a)
    assert max(len(v) for v in by_institution.values()) >= 3          # several accounts per institution
    assert {a["currency"] for a in accounts} == {"EUR", "USD"}        # more than one currency
    assert any(a["available_from"] and a["available_from"] > date.today().isoformat() for a in accounts)  # locked
    assert any(a["type"] == "loan" and a["balance"] < 0 for a in accounts)
    assert any(a["archived"] and a["balance"] == 0 for a in accounts)
    summary = {c["currency"]: c for c in client.get("/api/summary").json()}
    assert summary["USD"]["available"] < summary["USD"]["total"]
    history = client.get("/api/history?currency=EUR").json()
    assert len(history["dates"]) >= 12


def test_demo_reset_undoes_changes(client):
    """Resetting the demo brings back the original sample data."""
    client.post("/api/demo")
    first = client.get("/api/accounts").json()[0]
    client.delete(f"/api/accounts/{first['id']}")
    assert len(client.get("/api/accounts").json()) == len(SAMPLES) - 1
    assert client.post("/api/demo/reset").status_code == 204
    assert len(client.get("/api/accounts").json()) == len(SAMPLES)


def test_reset_needs_demo_mode(client):
    """Resetting the demo outside demo mode is refused."""
    assert client.post("/api/demo/reset").status_code == 409


def test_demo_link(client):
    """The /demo link starts the demo in the given language."""
    response = client.get("/demo?lang=es", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert client.get("/api/about").json()["demo"] is True
    assert any(a["name"] == "Hipoteca" for a in client.get("/api/accounts").json())


def test_demo_needs_login(tmp_path):
    """With a login set, the demo needs it too (single-user mode)."""
    app = create_app(Settings(data_dir=tmp_path, auth_username="u", auth_password="p"))
    with TestClient(app) as c:
        assert c.post("/api/demo").status_code == 401
        assert c.get("/demo", follow_redirects=False).headers["location"] == "/login"


def test_demo_combined_total_in_default_currency(tmp_path):
    """The demo's total in the default currency asks only for the exchange rate."""
    asked = []

    def fetch(base, quote, day):
        asked.append((base, quote, day))
        return Decimal("0.9"), date.today()

    with TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=fetch)) as c:
        c.post("/api/demo")
        combined = c.get("/api/summary/combined").json()
        summary = {s["currency"]: s for s in c.get("/api/summary").json()}
    assert combined["missing"] == []
    expected = summary["EUR"]["total"] + summary["USD"]["total"] * Decimal("0.9")  # in cents
    assert combined["total"] == int(expected.to_integral_value(ROUND_HALF_UP))
    assert asked == [("USD", "EUR", None)]  # only the currencies are asked for, never amounts


def test_demo_combined_total_offline(tmp_path):
    """The demo's combined total works without network, with its sample rate."""
    def offline(base, quote, day):
        raise OSError("no network")

    with TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=offline)) as c:
        c.post("/api/demo")
        combined = c.get("/api/summary/combined").json()
    assert combined["missing"] == [] and combined["rates"][0]["stale"] is True
