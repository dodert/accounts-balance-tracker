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
    assert about["schema"] == "0001" and about["default_currency"] == "EUR" and about["auth"] is False
    page = client.get("/")
    assert page.status_code == 200 and "{{ASSET_VERSION}}" not in page.text


def test_account_crud(client):
    account = make_account(client, "  Main  ", type="savings", institution="ING")
    assert account["name"] == "Main" and account["currency"] == "EUR" and account["balance"] is None
    assert client.post("/api/accounts", json={"name": "Main"}).status_code == 409
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
