from app.csv_io import parse_amount


def test_parse_amount():
    """Amounts are read in both 1.234,56 and 1,234.56 styles, with or without a symbol."""
    assert str(parse_amount("1.234,56")) == "1234.56"
    assert str(parse_amount("1,234.56")) == "1234.56"
    assert str(parse_amount("-50")) == "-50.00"
    assert str(parse_amount("12,5 €")) == "12.50"


def test_import_creates_accounts_and_never_overwrites_balances(client):
    """A CSV import creates accounts and balances, lists bad lines and never overwrites a balance."""
    csv = (
        "cuenta;tipo;moneda;fecha;saldo;nota\n"
        "ING;savings;EUR;31/01/2026;1.000,50;\n"
        "ING;savings;EUR;2026-02-28;1200;nómina\n"
        "Broker;investment;;2026-02-28;300;\n"
        ";;;2026-02-28;1;\n"
        "ING;;;2026-13-01;5;\n"
        "Bad;weird;;2026-02-01;5;\n"
        "\n"
    ).encode("cp1252")
    res = client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")}).json()
    assert (res["accounts_created"], res["balances_created"], res["duplicates_count"]) == (2, 3, 0)
    assert len(res["errors"]) == 3 and res["errors"][0].startswith("Line 5:")

    # A balance already there for that account and date is not changed: the row is skipped and listed
    again = client.post("/api/import/csv", files={"file": ("b.csv", b"account,date,amount\ning,2026-02-28,1300\ning,2026-03-31,1400\ning,2026-03-31,1500\n", "text/csv")}).json()
    assert (again["accounts_created"], again["balances_created"], again["duplicates_count"]) == (0, 1, 2)
    assert again["duplicates"] == ["Line 2: ING: there is already a balance on 2026-02-28",
                                   "Line 4: ING: there is already a balance on 2026-03-31"]

    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    assert accounts["ING"]["type"] == "savings" and accounts["ING"]["balance"] == 140000  # the API counts in cents
    assert accounts["Broker"]["currency"] == "EUR"
    balances = client.get(f"/api/accounts/{accounts['ING']['id']}/balances").json()
    assert {b["date"]: (b["amount"], b["note"]) for b in balances}["2026-02-28"] == (120000, "nómina")


def test_export_round_trip(client, tmp_path):
    """A CSV export imported into another database gives the same accounts and balances."""
    a = client.post("/api/accounts", json={"name": "Cash, wallet", "type": "cash", "available_from": "2027-01-01"}).json()
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": 1250, "note": "x"})
    res = client.get("/api/export/csv")
    assert res.headers["content-disposition"].startswith("attachment")
    assert res.text.splitlines() == ["account,type,currency,institution,date,amount,note,available_from", '"Cash, wallet",cash,EUR,,2026-01-01,12.50,x,2027-01-01']

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    other = TestClient(create_app(Settings(data_dir=tmp_path / "other")))
    out = other.post("/api/import/csv", files={"file": ("e.csv", res.content, "text/csv")}).json()
    assert (out["accounts_created"], out["balances_created"], out["errors"]) == (1, 1, [])
    copy = other.get("/api/accounts").json()[0]
    assert copy["type"] == "cash" and copy["available_from"] == "2027-01-01"


def test_import_rejects_files_without_required_columns(client):
    """A CSV without the required columns is refused."""
    res = client.post("/api/import/csv", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert res.status_code == 422 and "account" in res.json()["detail"]


def test_import_finds_accounts_by_name_and_institution(client):
    """The CSV import matches accounts by name and institution."""
    csv = (
        "account,institution,date,amount\n"
        "Payroll,ING,2026-01-31,100\n"
        "Payroll,BBVA,2026-01-31,200\n"
        "payroll,bbva,2026-02-28,250\n"
        "Payroll,,2026-02-28,1\n"
    ).encode()
    res = client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")}).json()
    assert (res["accounts_created"], res["balances_created"]) == (2, 3)
    assert len(res["errors"]) == 1 and "add the institution" in res["errors"][0]
    balances = {a["institution"]: a["balance"] for a in client.get("/api/accounts").json()}
    assert balances == {"ING": 10000, "BBVA": 25000}  # cents

    # Without an institution column, a name that is unique still matches its account
    res = client.post("/api/import/csv", files={"file": ("c.csv", b"account,date,amount\nSolo,2026-01-31,5\nSolo,2026-02-28,6\n", "text/csv")}).json()
    assert (res["accounts_created"], res["balances_created"], res["errors"]) == (1, 2, [])



def test_export_neutralizes_formulas_and_import_reads_them_back(client, tmp_path):
    """Texts that a spreadsheet would run as formulas are made safe on export and read back on import."""
    account = client.post("/api/accounts", json={"name": "=HYPERLINK(\"http://x\")", "institution": "@bank"}).json()
    client.post(f"/api/accounts/{account['id']}/balances", json={"date": "2026-01-31", "amount": -45000, "note": "+1 call"})
    text = client.get("/api/export/csv").text
    row = text.splitlines()[1]
    assert row.startswith("\"'=HYPERLINK(") and ",'@bank," in row and ",'+1 call," in row
    assert ",-450.00," in row  # amounts are numbers, left as they are

    # Imported back, the same account is found and the texts are as they were
    res = client.post("/api/import/csv", files={"file": ("e.csv", text.encode(), "text/csv")}).json()
    assert res["accounts_created"] == 0 and res["duplicates_count"] == 1 and not res["errors"]
    balances = client.get(f"/api/accounts/{account['id']}/balances").json()
    assert balances[0]["note"] == "+1 call"
    # A name that only starts with a quote keeps it
    from app.csv_io import unsafe_text
    assert unsafe_text("'quoted") == "'quoted"
