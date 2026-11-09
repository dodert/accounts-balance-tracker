"""The server's clock is UTC; "today" is the user's date (X-Local-Date header or local_date), else the UTC date."""
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.clock import parse_local_date, use_utc
from app.config import Settings
from app.main import create_app
from tests.test_fx import FakeRates

UTC_NOON = datetime(2026, 3, 10, 12, 0, tzinfo=UTC)


def utc_today() -> date:
    return datetime.now(UTC).date()


@pytest.mark.parametrize("value, expected", [
    ("2026-03-10", date(2026, 3, 10)),
    ("2026-03-11", date(2026, 3, 11)),  # already tomorrow east of UTC (up to UTC+14)
    ("2026-03-09", date(2026, 3, 9)),  # still yesterday west of UTC (down to UTC-12)
    ("2026-03-12", None),  # no time zone has that date now
    ("2026-03-08", None),
    ("10/03/2026", None),
    ("", None),
    (None, None),
])
def test_the_browser_date_is_used_only_if_some_time_zone_has_it(value, expected):
    """The browser's date is accepted only when it is a real date within a day of the UTC date."""
    assert parse_local_date(value, UTC_NOON) == expected


def test_available_today_follows_the_browser_date(client):
    """An account available from tomorrow (server's day) counts as available when the browser is already there."""
    user_day = utc_today() + timedelta(days=1)  # e.g. just after midnight in UTC+2 while the server is in UTC
    if date.today() >= user_day:
        pytest.skip("the server's own time zone is already on that day")
    a = client.post("/api/accounts", json={"name": "Deposit", "available_from": user_day.isoformat()}).json()
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": 100})

    assert client.get("/api/summary").json()[0]["available"] == 0  # no header: the server's day
    headers = {"X-Local-Date": user_day.isoformat()}
    assert client.get("/api/summary", headers=headers).json()[0]["available"] == 100
    too_far = {"X-Local-Date": (utc_today() + timedelta(days=5)).isoformat()}
    assert client.get("/api/summary", headers=too_far).json()[0]["available"] == 0  # ignored


def test_todays_rate_is_stored_also_when_the_browser_is_a_day_ahead(tmp_path):
    """With the browser past midnight and the server not, today's rate is still asked once, for the browser's day."""
    rates = FakeRates()
    client = TestClient(create_app(Settings(data_dir=tmp_path, default_currency="EUR"), fx_fetcher=rates))
    user_day = utc_today() + timedelta(days=1)
    headers = {"X-Local-Date": user_day.isoformat()}
    for name, currency in (("Euros", "EUR"), ("Dollars", "USD")):
        a = client.post("/api/accounts", json={"name": name, "currency": currency}).json()
        client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": 100})

    client.get("/api/summary/combined", headers=headers)
    client.get("/api/summary/combined", headers=headers)
    assert rates.calls == [("USD", "EUR", None)]  # the latest rate, asked once
    rate = client.get("/api/fx", params={"from": "USD", "to": "EUR", "date": user_day.isoformat()}, headers=headers).json()
    assert rate["day"] == user_day.isoformat() and len(rates.calls) == 1


def test_the_server_runs_in_utc_whatever_tz_says(monkeypatch):
    """An install that still sets TZ (older templates had it) runs in UTC anyway."""
    import time
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")  # UTC+14
    time.tzset()
    try:
        assert time.localtime().tm_gmtoff == 14 * 3600
        use_utc()
        assert time.localtime().tm_gmtoff == 0
        assert datetime.now().astimezone().utcoffset() == timedelta(0)
    finally:
        monkeypatch.undo()
        use_utc()


def test_the_export_file_name_uses_the_users_date(client):
    """A download link sends the user's date as local_date, and the file is named with it."""
    user_day = (utc_today() + timedelta(days=1)).isoformat()
    for kind in ("csv", "json"):
        res = client.get(f"/api/export/{kind}", params={"local_date": user_day})
        assert f"balances-{user_day}.{kind}" in res.headers["content-disposition"]
