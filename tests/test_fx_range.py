"""Exchange rates for many days in one request, never asked while the user's database is open."""
import io
import json
import sqlite3
import threading
import urllib.error
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.config import Settings
from app.fx import frankfurter, resolve
from app.main import create_app
from tests.test_multi_user import admin_client, invite, rates_app, sign_up


class RangeRates:
    """A rate service that answers a range of days in one call (like Frankfurter's time series)."""

    def __init__(self):
        self.calls, self.range_calls = [], []

    def __call__(self, base, quote, day):
        self.calls.append((base, quote, day))
        return Decimal("0.9"), day or date.today()

    def days(self, base, quote, wanted):
        self.range_calls.append((base, quote, frozenset(wanted)))
        return {d: (Decimal("0.9"), d) for d in wanted}


def weekly_usd(client, weeks, start=date(2020, 1, 6)):
    account = client.post("/api/accounts", json={"name": "Dollars", "currency": "USD"}).json()
    lines = ["account,currency,date,amount"] + [f"Dollars,USD,{start + timedelta(weeks=n)},100" for n in range(weeks)]
    client.post("/api/import/csv", files={"file": ("x.csv", "\n".join(lines).encode())})
    client.post("/api/accounts", json={"name": "Euros"})
    return account


def test_resolve_takes_the_last_working_day():
    friday, monday = date(2024, 1, 5), date(2024, 1, 8)
    published = [(friday, Decimal("0.91")), (monday, Decimal("0.92"))]
    saturday, before = date(2024, 1, 6), date(2024, 1, 1)
    assert resolve(published, {saturday, monday, before}) == {saturday: (Decimal("0.91"), friday),
                                                              monday: (Decimal("0.92"), monday)}


def test_frankfurter_asks_a_range_with_currencies_and_dates_only(monkeypatch):
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        body = {"amount": 1.0, "base": "USD", "start_date": "2023-12-29", "end_date": "2024-01-08",
                "rates": {"2023-12-29": {"EUR": 0.90}, "2024-01-05": {"EUR": 0.91}, "2024-01-08": {"EUR": 0.92}}}
        return io.BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    fetch = frankfurter("https://api.frankfurter.app/")
    got = fetch.days("USD", "EUR", {date(2024, 1, 6), date(2024, 1, 8)})
    assert got == {date(2024, 1, 6): (Decimal("0.91"), date(2024, 1, 5)),
                   date(2024, 1, 8): (Decimal("0.92"), date(2024, 1, 8))}
    # One request: a week before the first day to the last one, and only the two currencies
    assert seen == ["https://api.frankfurter.app/2023-12-30..2024-01-08?from=USD&to=EUR"]


def test_a_service_without_ranges_is_asked_day_by_day(monkeypatch, tmp_path):
    seen = []

    def urlopen(request, timeout):
        seen.append(request.full_url)
        if ".." in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        day = request.full_url.rsplit("/", 1)[1].split("?")[0]
        return io.BytesIO(json.dumps({"date": day, "rates": {"EUR": 0.9}}).encode())

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR",
                                            fx_url="https://rates.example")))
    weekly_usd(client, 3)
    h = client.get("/api/history/converted").json()
    assert h["converted"] == ["USD"] and h["stale"] is False
    assert len([u for u in seen if ".." in u]) == 1 and len([u for u in seen if ".." not in u]) == 3


def test_single_user_converted_history_asks_once(tmp_path):
    rates = RangeRates()
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=rates))
    weekly_usd(client, 60)
    h = client.get("/api/history/converted").json()
    assert h["converted"] == ["USD"] and h["stale"] is False and len(h["dates"]) == 60
    assert len(rates.range_calls) == 1 and rates.calls == []
    assert len(rates.range_calls[0][2]) == 60
    client.get("/api/history/converted")
    assert len(rates.range_calls) == 1  # stored


def test_multi_user_long_history_takes_one_request_and_never_writes_the_users_database(tmp_path):
    rates = RangeRates()
    app = rates_app(tmp_path, rates)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    weekly_usd(ana, 260)
    database = tmp_path / "users" / "1.db.enc"
    before = database.read_bytes()
    h = ana.get("/api/history/converted").json()
    assert h["converted"] == ["USD"] and h["stale"] is False and len(h["dates"]) == 260
    assert ana.get("/api/summary/combined").json()["missing"] == []
    with sqlite3.connect(tmp_path / "rates.db") as db:
        assert db.execute("SELECT count FROM requests").fetchone()[0] == 2  # the history, and today's rate
    assert database.read_bytes() == before  # the rates are kept only in the shared store


def test_the_user_is_not_kept_waiting_while_rates_are_asked(tmp_path):
    entered, release = threading.Event(), threading.Event()

    class SlowRates(RangeRates):
        def days(self, base, quote, wanted):
            entered.set()
            assert release.wait(10)
            return super().days(base, quote, wanted)

    app = rates_app(tmp_path, SlowRates())
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    weekly_usd(ana, 5)
    same_user = TestClient(app, cookies=dict(ana.cookies))
    result = {}
    worker = threading.Thread(target=lambda: result.update(h=ana.get("/api/history/converted")))
    worker.start()
    try:
        assert entered.wait(10)
        # The rate service hasn't answered yet, and the user's other requests are answered meanwhile
        other = threading.Thread(target=lambda: result.update(accounts=same_user.get("/api/accounts")))
        other.start()
        other.join(5)
        assert not other.is_alive() and result["accounts"].status_code == 200
    finally:
        release.set()
        worker.join(10)
    assert result["h"].status_code == 200 and result["h"].json()["converted"] == ["USD"]


def test_multi_user_demo_uses_its_sample_rate_without_asking(tmp_path):
    rates = RangeRates()
    visitor = TestClient(rates_app(tmp_path, rates))
    visitor.post("/api/demo")
    assert visitor.get("/api/summary/combined").json()["missing"] == []
    assert visitor.get("/api/history/converted").json()["missing"] == []
    assert rates.calls == [] and rates.range_calls == []
