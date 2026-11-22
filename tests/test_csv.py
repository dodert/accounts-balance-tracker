import pytest

from app.csv_io import parse_amount


@pytest.mark.parametrize("text, decimal, amount", [
    ("1.234", ",", "1234.0000"), ("1,234", ".", "1234.0000"), ("12.500", ",", "12500.0000"),
    ("12,500", ".", "12500.0000"), ("1.234,56", ",", "1234.5600"), ("1,234.56", ".", "1234.5600"),
    ("1234.56", ".", "1234.5600"), ("-50", ",", "-50.0000"), ("12,5 €", ",", "12.5000"), ("1,2345", ",", "1.2345"),
    ("1.2345", ".", "1.2345"), ("1.234.567,89995", ",", "1234567.9000"),
    ("0,00005", ",", "0.0001"), ("0.00005", ".", "0.0001"),  # past 4 decimals: half up, as the page
])
def test_parse_amount(text, decimal, amount):
    """Amounts are read with the decimal separator given; the other one groups thousands."""
    assert str(parse_amount(text, decimal)) == amount


@pytest.mark.parametrize("text, decimal", [("1,234.56", ","), ("1.234,56", "."), ("12.50", ","), ("1,5", "."),
                                           ("1.2.3", ","), ("abc", "."), ("-", ","),
                                           ("500000000000", ".")])  # past the largest amount
def test_parse_amount_that_does_not_fit_the_separator_is_an_error(text, decimal):
    """An amount that doesn't fit the decimal separator given is an error."""
    with pytest.raises(ValueError, match="invalid amount"):
        parse_amount(text, decimal)


def test_import_reads_amounts_with_the_separator_sent(client):
    """The CSV import reads amounts with the decimal separator sent (a point when none) and names bad lines."""
    csv = b"account,date,amount\nA,2026-01-31,1.234\nA,2026-02-28,\"1.234,56\"\nA,2026-03-31,\"1,234.56\"\n"
    comma = client.post("/api/import/csv", files={"file": ("a.csv", csv, "text/csv")}, data={"decimal": ","}).json()
    assert comma["balances_created"] == 2 and comma["errors"] == [
        "Line 4: invalid amount '1,234.56' (decimal separator ',')"]
    balances = {b["date"]: b["amount"] for b in client.get("/api/accounts/1/balances").json()}
    assert balances == {"2026-01-31": 12340000, "2026-02-28": 12345600}  # ten-thousandths

    csv = b"account,date,amount\nB,2026-01-31,\"1,234\"\nB,2026-02-28,1234.5\n"
    point = client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")}).json()
    assert point["balances_created"] == 2 and point["errors"] == []
    assert client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")},
                       data={"decimal": ";"}).status_code == 422


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
    res = client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")}, data={"decimal": ","}).json()
    assert (res["accounts_created"], res["balances_created"], res["duplicates_count"]) == (2, 3, 0)
    assert len(res["errors"]) == 3 and res["errors"][0].startswith("Line 5:")

    # A balance already there for that account and date is not changed: the row is skipped and listed
    again = client.post("/api/import/csv", files={"file": ("b.csv", b"account,date,amount\ning,2026-02-28,1300\ning,2026-03-31,1400\ning,2026-03-31,1500\n", "text/csv")}).json()
    assert (again["accounts_created"], again["balances_created"], again["duplicates_count"]) == (0, 1, 2)
    assert again["duplicates"] == ["Line 2: ING: there is already a balance on 2026-02-28",
                                   "Line 4: ING: there is already a balance on 2026-03-31"]

    accounts = {a["name"]: a for a in client.get("/api/accounts").json()}
    assert accounts["ING"]["type"] == "savings" and accounts["ING"]["balance"] == 14000000  # the API counts in ten-thousandths
    assert accounts["Broker"]["currency"] == "EUR"
    balances = client.get(f"/api/accounts/{accounts['ING']['id']}/balances").json()
    assert {b["date"]: (b["amount"], b["note"]) for b in balances}["2026-02-28"] == (12000000, "nómina")


def test_export_round_trip(client, tmp_path):
    """A CSV export imported into another database gives the same accounts and balances."""
    a = client.post("/api/accounts", json={"name": "Cash, wallet", "type": "cash", "available_from": "2027-01-01"}).json()
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": 125000, "note": "x"})
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


def test_export_and_import_keep_four_decimals(client, tmp_path):
    """A balance with four decimals is exported as such and imported back without losing them."""
    a = client.post("/api/accounts", json={"name": "Fund"}).json()
    client.post(f"/api/accounts/{a['id']}/balances", json={"date": "2026-01-01", "amount": 12345})  # 1.2345
    row = client.get("/api/export/csv").text.splitlines()[1]
    assert ",2026-01-01,1.2345," in row

    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app
    other = TestClient(create_app(Settings(data_dir=tmp_path / "other")))
    other.post("/api/import/csv", files={"file": ("e.csv", client.get("/api/export/csv").content, "text/csv")})
    assert other.get("/api/accounts").json()[0]["balance"] == 12345


def test_import_rejects_files_without_required_columns(client):
    """A CSV without the required columns is refused."""
    res = client.post("/api/import/csv", files={"file": ("x.csv", b"foo,bar\n1,2\n", "text/csv")})
    assert res.status_code == 422 and "account" in res.json()["detail"]


def test_import_finds_accounts_by_name_and_institution(client):
    """The CSV import matches accounts by name and institution."""
    csv = (
        b"account,institution,date,amount\n"
        b"Payroll,ING,2026-01-31,100\n"
        b"Payroll,BBVA,2026-01-31,200\n"
        b"payroll,bbva,2026-02-28,250\n"
        b"Payroll,,2026-02-28,1\n"
    )
    res = client.post("/api/import/csv", files={"file": ("b.csv", csv, "text/csv")}).json()
    assert (res["accounts_created"], res["balances_created"]) == (2, 3)
    assert len(res["errors"]) == 1 and "add the institution" in res["errors"][0]
    balances = {a["institution"]: a["balance"] for a in client.get("/api/accounts").json()}
    assert balances == {"ING": 1000000, "BBVA": 2500000}  # ten-thousandths

    # Without an institution column, a name that is unique still matches its account
    res = client.post("/api/import/csv", files={"file": ("c.csv", b"account,date,amount\nSolo,2026-01-31,5\nSolo,2026-02-28,6\n", "text/csv")}).json()
    assert (res["accounts_created"], res["balances_created"], res["errors"]) == (1, 2, [])



def test_export_neutralizes_formulas_and_import_reads_them_back(client, tmp_path):
    """Texts that a spreadsheet would run as formulas are made safe on export and read back on import."""
    account = client.post("/api/accounts", json={"name": "=HYPERLINK(\"http://x\")", "institution": "@bank"}).json()
    client.post(f"/api/accounts/{account['id']}/balances", json={"date": "2026-01-31", "amount": -4500000, "note": "+1 call"})
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



def test_import_finds_accounts_it_created_and_existing_ones_by_name(client):
    """Rows naming an account created earlier in the file, or an existing one, in any case, use that account (#172)."""
    client.post("/api/accounts", json={"name": "Savings", "currency": "EUR"})
    csv = (b"account,institution,date,amount\nNew,,2026-01-31,1\nNEW,,2026-02-28,2\nsavings,,2026-01-31,3\n"
           b"Fund,Bank A,2026-01-31,4\nFund,Bank B,2026-01-31,5\nfund,bank a,2026-02-28,6\n")
    res = client.post("/api/import/csv", files={"file": ("a.csv", csv, "text/csv")}).json()
    assert res["errors"] == [] and res["accounts_created"] == 3 and res["balances_created"] == 6
    accounts = {(a["name"], a["institution"]): a["id"] for a in client.get("/api/accounts").json()}
    assert len(accounts) == 4
    count = lambda key: len(client.get(f"/api/accounts/{accounts[key]}/balances").json())  # noqa: E731
    assert count(("New", None)) == 2 and count(("Savings", None)) == 1
    assert count(("Fund", "Bank A")) == 2 and count(("Fund", "Bank B")) == 1


def test_import_refuses_a_file_with_too_many_new_accounts(client, monkeypatch):
    """A CSV file that would create more than MAX_NEW_ACCOUNTS accounts is refused whole (#172)."""
    import app.csv_io as csv_io
    monkeypatch.setattr(csv_io, "MAX_NEW_ACCOUNTS", 3)
    csv = b"account,date,amount\nA,2026-01-31,1\nB,2026-01-31,1\nC,2026-01-31,1\nA,2026-02-28,1\nD,2026-01-31,1\n"
    res = client.post("/api/import/csv", files={"file": ("a.csv", csv, "text/csv")})
    assert res.status_code == 422 and "more than 3 accounts" in res.json()["detail"]
    assert client.get("/api/accounts").json() == []  # nothing stored
