def make_account(client, name="Bank", **extra):
    res = client.post("/api/accounts", json={"name": name, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def add(client, account_id, day, amount, note=None):
    res = client.post(f"/api/accounts/{account_id}/balances", json={"date": day, "amount": amount, "note": note})
    assert res.status_code == 201, res.text
    return res.json()


def test_health_and_about(client):
    assert client.get("/api/health").json()["status"] == "ok"
    about = client.get("/api/about").json()
    assert about["schema"] == "0005" and about["default_currency"] == "EUR" and about["auth"] is False
    page = client.get("/")
    assert page.status_code == 200 and "{{ASSET_VERSION}}" not in page.text


def test_account_crud(client):
    account = make_account(client, "  Main  ", type="savings", institution="ING")
    assert account["name"] == "Main" and account["currency"] == "EUR" and account["balance"] is None
    assert client.post("/api/accounts", json={"name": "Main", "institution": "ING"}).status_code == 409
    assert client.post("/api/accounts", json={"name": "X", "currency": "euro"}).status_code == 422
    assert client.post("/api/accounts", json={"name": "X", "type": "nope"}).status_code == 422

    updated = client.patch(f"/api/accounts/{account['id']}", json={"currency": "usd", "institution": "", "archived": True}).json()
    assert updated["currency"] == "USD" and updated["institution"] is None and updated["archived"] is True
    assert client.get("/api/accounts", params={"archived": False}).json() == []
    assert len(client.get("/api/accounts").json()) == 1

    add(client, account["id"], "2026-01-01", 10)
    assert client.delete(f"/api/accounts/{account['id']}").status_code == 204
    assert client.get(f"/api/accounts/{account['id']}").status_code == 404
    assert client.get("/api/history").json()["dates"] == []


def test_balances_one_per_day(client):
    account = make_account(client)
    add(client, account["id"], "2026-01-31", "1000.50")
    add(client, account["id"], "2026-02-28", 1200)
    again = add(client, account["id"], "2026-02-28", 1250.25, "bonus")  # same day: replaced
    balances = client.get(f"/api/accounts/{account['id']}/balances").json()
    assert [(b["date"], b["amount"]) for b in balances] == [("2026-02-28", 1250.25), ("2026-01-31", 1000.5)]
    assert again["note"] == "bonus"

    out = client.get(f"/api/accounts/{account['id']}").json()
    assert (out["balance"], out["balance_date"], out["previous_balance"]) == (1250.25, "2026-02-28", 1000.5)
    assert (out["first_balance"], out["first_date"]) == (1000.5, "2026-01-31")

    first = balances[1]["id"]
    assert client.patch(f"/api/balances/{first}", json={"date": "2026-02-28"}).status_code == 409
    assert client.patch(f"/api/balances/{first}", json={"amount": 999}).json()["amount"] == 999
    assert client.delete(f"/api/balances/{first}").status_code == 204
    assert client.delete(f"/api/balances/{first}").status_code == 404
    assert client.post(f"/api/accounts/{account['id']}/balances", json={"date": "2026-01-01", "amount": "1.234"}).status_code == 422


def test_bulk_update(client):
    a, b = make_account(client, "A"), make_account(client, "B")
    res = client.post("/api/balances/bulk", json={"date": "2026-03-01", "items": [
        {"account_id": a["id"], "amount": 100}, {"account_id": b["id"], "amount": -20}]})
    assert res.json() == {"created": 2, "updated": 0}
    res = client.post("/api/balances/bulk", json={"date": "2026-03-01", "items": [{"account_id": a["id"], "amount": 150}]})
    assert res.json() == {"created": 0, "updated": 1}
    assert client.post("/api/balances/bulk", json={"date": "2026-03-01", "items": [{"account_id": 999, "amount": 1}]}).status_code == 422
    dup = [{"account_id": a["id"], "amount": 1}] * 2
    assert client.post("/api/balances/bulk", json={"date": "2026-03-01", "items": dup}).status_code == 422
    assert client.get("/api/summary").json()[0]["total"] == 130


def test_summary_and_history(client):
    bank = make_account(client, "Bank")
    broker = make_account(client, "Broker", type="investment")
    usd = make_account(client, "US", currency="USD")
    add(client, bank["id"], "2026-01-01", 1000)
    add(client, broker["id"], "2026-01-15", 500)
    add(client, bank["id"], "2026-02-01", 800)
    add(client, usd["id"], "2026-02-01", 50)

    summary = {c["currency"]: c for c in client.get("/api/summary").json()}
    assert summary["EUR"]["total"] == 1300 and summary["EUR"]["date"] == "2026-02-01"
    assert summary["EUR"]["previous_total"] == 1500 and summary["EUR"]["previous_date"] == "2026-01-15"
    assert summary["EUR"]["first_total"] == 1000 and summary["EUR"]["first_date"] == "2026-01-01"
    assert summary["USD"]["total"] == 50 and summary["USD"]["previous_total"] is None and summary["USD"]["first_total"] is None
    assert client.get("/api/currencies").json() == ["EUR", "USD"]

    h = client.get("/api/history", params={"currency": "eur"}).json()
    assert h["currency"] == "EUR"
    assert h["dates"] == ["2026-01-01", "2026-01-15", "2026-02-01"]
    assert h["total"] == [1000, 1500, 1300]
    values = {s["name"]: s["values"] for s in h["accounts"]}
    assert values == {"Bank": [1000, 1000, 800], "Broker": [None, 500, 500]}

    # A range keeps the balances recorded before it as the starting point
    h = client.get("/api/history", params={"currency": "EUR", "since": "2026-01-20"}).json()
    assert h["dates"] == ["2026-01-20", "2026-02-01"] and h["total"] == [1500, 1300]
    # Default currency of the chart: the one with most accounts
    assert client.get("/api/history").json()["currency"] == "EUR"


def test_archived_accounts_still_count_in_history(client):
    old = make_account(client, "Old")
    add(client, old["id"], "2026-01-01", 300)
    add(client, old["id"], "2026-02-01", 0)
    client.patch(f"/api/accounts/{old['id']}", json={"archived": True})
    h = client.get("/api/history").json()
    assert h["total"] == [300, 0]
    assert client.get("/api/summary").json()[0]["accounts"] == 0


def test_cross_site_post_blocked(client):
    res = client.post("/api/accounts", json={"name": "X"}, headers={"sec-fetch-site": "cross-site"})
    assert res.status_code == 403
    # Same site but another origin (e.g. another app on the same host, another port): blocked too
    res = client.post("/api/accounts", json={"name": "X"}, headers={"sec-fetch-site": "same-site"})
    assert res.status_code == 403
    # Browsers without Sec-Fetch-Site: by the Origin they send
    for origin in ("http://evil.example", "http://testserver:9000", "null"):
        assert client.post("/api/accounts", json={"name": "X"}, headers={"origin": origin}).status_code == 403
    # The app itself, a link typed by the user and clients that send neither header still work
    assert client.post("/api/accounts", json={"name": "A"}, headers={"sec-fetch-site": "same-origin"}).status_code == 201
    assert client.post("/api/accounts", json={"name": "B"}, headers={"sec-fetch-site": "none"}).status_code == 201
    assert client.post("/api/accounts", json={"name": "C"}, headers={"origin": "http://testserver"}).status_code == 201
    assert client.post("/api/accounts", json={"name": "D"}).status_code == 201
    # Reading is never blocked
    assert client.get("/api/accounts", headers={"sec-fetch-site": "cross-site"}).status_code == 200


def test_account_notes_have_a_maximum_length(client):
    assert client.post("/api/accounts", json={"name": "X", "notes": "a" * 5001}).status_code == 422
    account = make_account(client, "Y", notes="a" * 5000)
    assert client.patch(f"/api/accounts/{account['id']}", json={"notes": "b" * 5001}).status_code == 422


def test_account_names_are_unique_per_institution(client):
    ing = make_account(client, "Payroll", institution="ING")
    bbva = make_account(client, "Payroll", institution="BBVA")
    plain = make_account(client, "Payroll")
    assert len({ing["id"], bbva["id"], plain["id"]}) == 3
    assert client.post("/api/accounts", json={"name": "Payroll"}).status_code == 409
    assert client.post("/api/accounts", json={"name": "Payroll", "institution": "BBVA"}).status_code == 409
    assert client.patch(f"/api/accounts/{bbva['id']}", json={"institution": "ING"}).status_code == 409


def test_available_from(client):
    from datetime import date, timedelta

    future = (date.today() + timedelta(days=30)).isoformat()
    cash = make_account(client, "Cash")
    options = make_account(client, "Options", type="investment", available_from=future)
    vested = make_account(client, "Vested", available_from="2026-02-01")
    assert options["available_from"] == future
    for account, amount in ((cash, 100), (options, 1000), (vested, 50)):
        add(client, account["id"], "2026-01-01", amount)
    add(client, vested["id"], "2026-03-01", 60)

    summary = client.get("/api/summary").json()[0]
    assert summary["total"] == 1160 and summary["available"] == 160  # options not yet
    assert summary["first_total"] == 1150 and summary["first_available"] == 100

    h = client.get("/api/history").json()
    assert h["dates"] == ["2026-01-01", "2026-03-01"]
    assert h["total"] == [1150, 1160] and h["available"] == [100, 160]  # vested from 2026-02-01

    cleared = client.patch(f"/api/accounts/{options['id']}", json={"available_from": ""}).json()
    assert cleared["available_from"] is None
    assert client.get("/api/summary").json()[0]["available"] == 1160


def test_milestones(client):
    a = client.post("/api/accounts", json={"name": "Savings"}).json()
    r = client.post(f"/api/accounts/{a['id']}/milestones", json={"date": "2026-03-10", "amount": "12500", "description": "  New car "})
    assert r.status_code == 201
    m = r.json()
    assert (m["account_id"], m["date"], m["amount"], m["description"]) == (a["id"], "2026-03-10", 12500, "New car")
    assert client.post(f"/api/accounts/{a['id']}/milestones", json={"date": "2026-03-10", "amount": 1, "description": " "}).status_code == 422
    assert client.post("/api/accounts/999/milestones", json={"date": "2026-03-10", "amount": 1, "description": "x"}).status_code == 404

    m = client.put(f"/api/milestones/{m['id']}", json={"date": "2026-03-11", "amount": 12000, "description": "Car"}).json()
    assert (m["date"], m["amount"], m["description"]) == ("2026-03-11", 12000, "Car")
    assert [x["id"] for x in client.get("/api/milestones").json()] == [m["id"]]
    assert client.get("/api/milestones", params={"account_id": 999}).json() == []

    assert client.delete(f"/api/milestones/{m['id']}").status_code == 204
    assert client.get("/api/milestones").json() == []
    # Deleting the account deletes its milestones
    client.post(f"/api/accounts/{a['id']}/milestones", json={"date": "2026-03-10", "amount": 1, "description": "x"})
    client.delete(f"/api/accounts/{a['id']}")
    assert client.get("/api/milestones").json() == []


def test_a_postgres_database_url_stops_the_app_and_says_how_to_move_the_data(tmp_path):
    import pytest

    from app.config import Settings
    from app.main import create_app
    url = "postgresql+psycopg://balances:secret@db:5432/balances"
    for multi in (False, True):
        with pytest.raises(RuntimeError) as error:
            create_app(Settings(data_dir=tmp_path, database_url=url, multi_user=multi,
                                auth_username="admin", auth_password="admin-password"))
        message = str(error.value)
        assert "postgresql+psycopg" in message and "Export everything (JSON)" in message and "Import JSON" in message
        assert "secret" not in message  # the password in the URL is not written to the log
    assert not list(tmp_path.iterdir())  # nothing was created before stopping
