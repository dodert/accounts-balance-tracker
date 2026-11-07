import json
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.fx import frankfurter
from app.main import create_app


class FakeRates:
    """Stands in for the exchange-rate service; records what it was asked."""

    def __init__(self, rate="0.9"):
        self.rate, self.calls, self.down = Decimal(rate), [], False

    def __call__(self, base, quote, day):
        self.calls.append((base, quote, day))
        if self.down:
            raise OSError("service down")
        return self.rate, day or date.today() - timedelta(days=1)


@pytest.fixture
def fx(tmp_path):
    rates = FakeRates()
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=rates))
    for name, currency, amount in (("Euros", "EUR", 1000), ("Dollars", "USD", 500)):
        a = client.post("/api/accounts", json={"name": name, "currency": currency}).json()
        client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": amount})
    return client, rates


def test_combined_total_fetches_once_a_day(fx):
    client, rates = fx
    combined = client.get("/api/summary/combined").json()
    assert combined["currency"] == "EUR" and combined["total"] == 1450 and combined["missing"] == []
    assert [(r["base"], r["quote"], r["rate"], r["stale"]) for r in combined["rates"]] == [("USD", "EUR", 0.9, False)]
    client.get("/api/summary/combined")
    assert rates.calls == [("USD", "EUR", None)]  # stored: one call for the day

    rates.rate = Decimal("0.95")
    refreshed = client.post("/api/fx/refresh").json()
    assert refreshed["total"] == 1475 and len(rates.calls) == 2


def test_rate_for_a_past_day_is_stored_for_good(fx):
    client, rates = fx
    day = "2025-06-02"
    assert client.get("/api/fx", params={"from": "usd", "to": "EUR", "date": day}).json()["rate"] == 0.9
    client.get("/api/fx", params={"from": "USD", "to": "EUR", "date": day})
    assert rates.calls == [("USD", "EUR", date(2025, 6, 2))]


def test_service_down_uses_the_last_rate_or_leaves_the_currency_out(fx):
    client, rates = fx
    rates.down = True
    combined = client.get("/api/summary/combined").json()
    assert combined["total"] == 1000 and combined["missing"] == ["USD"]
    assert client.get("/api/fx", params={"from": "USD", "to": "EUR"}).status_code == 503

    rates.down = False
    client.get("/api/summary/combined")
    rates.down = True
    stale = client.post("/api/fx/refresh").json()
    assert stale["total"] == 1450 and stale["rates"][0]["stale"] is True


def test_only_the_currencies_and_date_are_sent(monkeypatch):
    """Amounts never leave the server: the request carries the two currencies and the date only."""
    seen = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"amount": 1.0, "base": "USD", "date": "2026-01-02", "rates": {"EUR": 0.91}}).encode()

    def urlopen(request, timeout):
        seen.append(request.full_url)
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    fetch = frankfurter("https://api.frankfurter.app/")
    assert fetch("USD", "EUR", date(2026, 1, 2)) == (Decimal("0.91"), date(2026, 1, 2))
    assert fetch("USD", "EUR", None)[0] == Decimal("0.91")
    assert seen == ["https://api.frankfurter.app/2026-01-02?from=USD&to=EUR", "https://api.frankfurter.app/latest?from=USD&to=EUR"]
