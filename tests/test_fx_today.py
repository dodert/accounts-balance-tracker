"""The chart of every currency in the default one uses today's rate, asked without the user's database open."""
import logging
import threading
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.test_fx import FakeRates
from tests.test_multi_user import admin_client, invite, rates_app, sign_up


def weekly_usd(client, weeks, start=date(2020, 1, 6)):
    client.post("/api/accounts", json={"name": "Dollars", "currency": "USD"})
    lines = ["account,currency,date,amount"] + [f"Dollars,USD,{start + timedelta(weeks=n)},100" for n in range(weeks)]
    client.post("/api/import/csv", files={"file": ("x.csv", "\n".join(lines).encode())})
    client.post("/api/accounts", json={"name": "Euros"})


def test_converted_history_uses_todays_rate_on_every_date(tmp_path, caplog):
    """The converted chart asks only today's rate once, for every date, and logs where it came from."""
    rates = FakeRates("0.9")
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=rates))
    weekly_usd(client, 60)
    caplog.set_level(logging.INFO, logger="app")
    logging.getLogger("app").propagate = True
    try:
        h = client.get("/api/history/converted").json()
        client.get("/api/history/converted")
    finally:
        logging.getLogger("app").propagate = False
    assert h["converted"] == ["USD"] and h["stale"] is False and len(h["dates"]) == 60
    dollars = next(a for a in h["accounts"] if a["name"] == "Dollars")
    assert set(dollars["values"]) == {9000}  # 100 USD at today's 0.9 on every date, in cents
    assert rates.calls == [("USD", "EUR", None)]  # only today's rate, once
    assert any("from the stored rates" in r.getMessage() for r in caplog.records)


def test_multi_user_asks_one_rate_and_never_writes_the_users_database(tmp_path):
    """Multi-user: one rate request, and the user's encrypted database is not written."""
    rates = FakeRates()
    app = rates_app(tmp_path, rates)
    admin = admin_client(app, tmp_path)
    ana, _ = sign_up(app, invite(admin))
    weekly_usd(ana, 260)
    database = tmp_path / "users" / "1.db.enc"
    before = database.read_bytes()
    h = ana.get("/api/history/converted").json()
    assert h["converted"] == ["USD"] and h["stale"] is False and len(h["dates"]) == 260
    assert ana.get("/api/summary/combined").json()["missing"] == []
    assert rates.calls == [("USD", "EUR", None)]
    assert database.read_bytes() == before  # rates are kept only in the shared store


def test_the_user_is_not_kept_waiting_while_the_rate_is_asked(tmp_path):
    """While a rate is being asked, the same user's other requests are not blocked."""
    entered, release = threading.Event(), threading.Event()

    class SlowRates(FakeRates):
        def __call__(self, base, quote, day):
            entered.set()
            assert release.wait(10)
            return super().__call__(base, quote, day)

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
    """Multi-user: the demo uses its sample rate and never asks the service."""
    rates = FakeRates()
    visitor = TestClient(rates_app(tmp_path, rates))
    visitor.post("/api/demo")
    assert visitor.get("/api/summary/combined").json()["missing"] == []
    assert visitor.get("/api/history/converted").json()["missing"] == []
    assert rates.calls == []
