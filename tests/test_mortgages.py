"""Mortgages: the French amortization engine (app/mortgage.py) and its API."""
from datetime import date
from decimal import Decimal as D

import pytest

from app.mortgage import Extra, Loan, installment_date, schedule

U = 10000  # ten-thousandths in the API


def loan(**extra):
    values = {"principal": D(100000), "rate": D(3), "discount": D(0), "term_months": 360,
              "first_payment": date(2020, 1, 31), "payment_day": 31} | extra
    return Loan(**values)


def test_payment_and_first_installment_match_the_textbook_values():
    """100,000 at 3 % over 30 years: 421.60 a month; the first installment is 250.00 interest and 171.60 principal."""
    s = schedule(loan(), [], date(2019, 1, 1))
    first = s.rows[0]
    assert (first.payment, first.interest, first.principal, first.outstanding) == (
        D("421.60"), D("250.00"), D("171.60"), D("99828.40"))
    assert len(s.rows) == 360 and s.rows[-1].outstanding == 0
    assert sum(r.principal for r in s.rows) == D(100000)  # the last installment takes the rounding residue
    assert s.totals["total_interest"] == sum(r.interest for r in s.rows)


def test_discount_lowers_the_applied_rate():
    """The applied rate is the rate minus the discount, for every installment."""
    s = schedule(loan(rate=D("3.5"), discount=D("0.5")), [], date(2019, 1, 1))
    assert s.totals["applied_rate"] == D(3) and s.rows[0].payment == D("421.60")


def test_zero_rate_splits_the_principal_evenly():
    """At 0 % every installment is principal: 1,200 over 12 months is 100 a month."""
    s = schedule(loan(principal=D(1200), rate=D(0), term_months=12), [], date(2019, 1, 1))
    assert [r.payment for r in s.rows] == [D(100)] * 12 and all(r.interest == 0 for r in s.rows)


def test_installment_dates_keep_the_payment_day_or_the_last_day_of_the_month():
    """Installments fall on the payment day, or on the month's last day when it is shorter."""
    m = loan()
    assert [installment_date(m, n) for n in (1, 2, 3, 14)] == [
        date(2020, 1, 31), date(2020, 2, 29), date(2020, 3, 31), date(2021, 2, 28)]


def test_installments_are_paid_once_their_date_comes():
    """An installment is paid on its date (the user's today), not before; the totals follow."""
    s = schedule(loan(), [], date(2020, 2, 29))
    assert [r.paid for r in s.rows[:3]] == [True, True, False]
    t = s.totals
    assert t["installments_paid"] == 2 and t["next_date"] == date(2020, 3, 31) and t["current_payment"] == D("421.60")
    assert t["outstanding"] == s.rows[1].outstanding and t["interest_paid"] == s.rows[0].interest + s.rows[1].interest


def test_reduce_term_keeps_the_payment_and_ends_sooner():
    """A prepayment that reduces the term keeps the payment, and the loan ends sooner with less interest."""
    base = schedule(loan(), [], date(2019, 1, 1))
    s = schedule(loan(), [Extra(date(2021, 1, 10), D(10000), "reduce_term", fee=D(100))], date(2021, 6, 1))
    assert {r.payment for r in s.rows[:-1]} == {D("421.60")}
    assert len(s.rows) == 308 and s.rows[-1].payment < D("421.60") and s.rows[-1].outstanding == 0
    assert s.rows[12].interest == D("219.78")  # installment 13 (31 Jan 2021): on what is left after the prepayment
    assert s.totals["total_interest"] < base.totals["total_interest"]
    assert (s.totals["prepaid"], s.totals["fees_paid"]) == (D(10000), D(100))
    assert sum(r.principal for r in s.rows) + D(10000) == D(100000)


def test_reduce_payment_keeps_the_term_and_lowers_the_payment():
    """A prepayment that reduces the payment keeps the end date and lowers the payment from the next installment."""
    s = schedule(loan(), [Extra(date(2021, 1, 10), D(10000), "reduce_payment")], date(2019, 1, 1))
    assert len(s.rows) == 360 and s.rows[11].payment == D("421.60") and s.rows[12].payment == D("378.54")
    assert s.rows[-1].date == date(2049, 12, 31) and s.rows[-1].outstanding == 0


def test_a_prepayment_on_an_installment_day_comes_after_it_and_one_past_the_end_applies_nothing():
    """A prepayment on an installment's date counts from the next one; more than what is left repays it all."""
    s = schedule(loan(principal=D(1200), rate=D(0), term_months=12),
                 [Extra(date(2020, 2, 29), D(5000), "reduce_term"), Extra(date(2021, 1, 1), D(10), "reduce_term")],
                 date(2019, 1, 1))
    assert len(s.rows) == 2
    first, late = s.prepayments
    assert (first.applied, first.before_installment) == (D(1000), None)
    assert (late.applied, late.outstanding) == (D(0), D(0))


def make(client, **extra):
    body = {"name": "Home", "institution": "Bank", "principal": 100000 * U, "rate": 3.5, "discount": 0.5,
            "term_months": 360, "first_payment": "2020-01-31"} | extra
    res = client.post("/api/mortgages", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def test_mortgage_crud_and_validation(client):
    """Mortgages can be created, edited and deleted; their fields are checked."""
    m = make(client)
    assert (m["currency"], m["payment_day"], m["rate"], m["discount"], m["principal"]) == ("EUR", 31, 3.5, 0.5, 100000 * U)
    for wrong in ({"principal": 0}, {"rate": -1}, {"discount": 4}, {"term_months": 0}, {"term_months": 601},
                  {"payment_day": 32}, {"principal": 12.5}):
        assert client.post("/api/mortgages", json={"name": "X", "principal": U, "rate": 3, "term_months": 12,
                                                   "first_payment": "2026-01-01"} | wrong).status_code == 422, wrong
    changed = client.put(f"/api/mortgages/{m['id']}", json={"name": "Home 2", "principal": 90000 * U, "rate": 2.95,
                                                             "term_months": 300, "first_payment": "2020-01-05",
                                                             "currency": "usd", "payment_day": 6}).json()
    assert (changed["name"], changed["rate"], changed["discount"], changed["currency"], changed["payment_day"]) == (
        "Home 2", 2.95, 0.0, "USD", 6)
    assert [x["id"] for x in client.get("/api/mortgages").json()] == [m["id"]]
    client.post(f"/api/mortgages/{m['id']}/prepayments", json={"date": "2021-01-01", "amount": U, "mode": "reduce_term"})
    assert client.delete(f"/api/mortgages/{m['id']}").status_code == 204
    assert client.get(f"/api/mortgages/{m['id']}").status_code == 404
    assert client.get(f"/api/mortgages/{m['id']}/schedule").status_code == 404


def test_schedule_endpoint_uses_the_users_today(client):
    """The schedule marks paid the installments up to the user's date (X-Local-Date), amounts in ten-thousandths."""
    m = make(client, first_payment="2026-01-31")
    s = client.get(f"/api/mortgages/{m['id']}/schedule", headers={"X-Local-Date": date.today().isoformat()}).json()
    row = s["rows"][0]
    assert (row["payment"], row["interest"], row["principal"]) == (4216000, 2500000, 1716000)
    paid = sum(1 for r in s["rows"] if r["paid"])
    assert paid == s["totals"]["installments_paid"] and all(r["paid"] for r in s["rows"][:paid])
    assert s["totals"]["applied_rate"] == 3.0 and s["mortgage"]["name"] == "Home"


def test_prepayments_crud_and_effect_on_the_schedule(client):
    """Prepayments can be added, edited and deleted, and lower the schedule's outstanding and interest."""
    m = make(client)
    before = client.get(f"/api/mortgages/{m['id']}/schedule").json()["totals"]
    url = f"/api/mortgages/{m['id']}/prepayments"
    for wrong in ({"amount": 0}, {"amount": -U}, {"fee": -1}, {"mode": "nope"}):
        assert client.post(url, json={"date": "2021-01-10", "amount": U, "mode": "reduce_term"} | wrong).status_code == 422
    p = client.post(url, json={"date": "2021-01-10", "amount": 10000 * U, "mode": "reduce_term", "fee": 100 * U,
                               "note": " "}).json()
    assert (p["mode"], p["fee"], p["note"]) == ("reduce_term", 100 * U, None)
    after = client.get(f"/api/mortgages/{m['id']}/schedule").json()
    assert after["totals"]["total_interest"] < before["total_interest"] and after["totals"]["installments"] == 308
    assert after["prepayments"][0]["applied"] == 10000 * U and after["prepayments"][0]["before_installment"] == 13
    edited = client.put(f"/api/prepayments/{p['id']}", json={"date": "2021-01-10", "amount": 10000 * U,
                                                             "mode": "reduce_payment"}).json()
    assert edited["mode"] == "reduce_payment" and edited["fee"] == 0
    assert client.get(url).json()[0]["mode"] == "reduce_payment"
    assert client.delete(f"/api/prepayments/{p['id']}").status_code == 204
    assert client.delete(f"/api/prepayments/{p['id']}").status_code == 404
    assert client.get(f"/api/mortgages/{m['id']}/schedule").json()["totals"] == before


def test_simulation_compares_both_modes_and_saves_nothing(client):
    """The simulator compares simulated prepayments (as entered and in both modes) with the real schedule, net of
    their fees, and stores nothing."""
    m = make(client, first_payment="2020-01-31")
    client.post(f"/api/mortgages/{m['id']}/prepayments", json={"date": "2021-01-10", "amount": 5000 * U, "mode": "reduce_term"})
    base = client.get(f"/api/mortgages/{m['id']}/schedule").json()
    body = {"prepayments": [{"date": "2030-03-10", "amount": 20000 * U, "mode": "reduce_payment", "fee": 200 * U}]}
    r = client.post(f"/api/mortgages/{m['id']}/simulate", json=body).json()
    assert r["base"] == base["totals"]
    term, payment = r["reduce_term"]["compared"], r["reduce_payment"]["compared"]
    assert r["as_entered"]["compared"] == payment
    assert term["interest_saved"] > payment["interest_saved"] > 0
    assert term["net_saved"] == term["interest_saved"] - 200 * U and term["fees"] == 200 * U
    assert term["months_saved"] > 0 and payment["months_saved"] == 0
    assert term["payment_after"] == term["payment_before"] and payment["payment_after"] < payment["payment_before"]
    assert len(r["reduce_term"]["outstanding"]) == r["reduce_term"]["totals"]["installments"]
    assert client.get(f"/api/mortgages/{m['id']}/prepayments").json()[0]["amount"] == 5000 * U  # only the real one
    assert len(client.get(f"/api/mortgages/{m['id']}/prepayments").json()) == 1
    assert client.post(f"/api/mortgages/{m['id']}/simulate", json={"prepayments": []}).status_code == 422
    assert client.post("/api/mortgages/999/simulate", json=body).status_code == 404


@pytest.mark.parametrize("mode", ["reduce_term", "reduce_payment"])
def test_principal_is_always_fully_repaid(mode):
    """Whatever the prepayments, installments plus prepayments repay exactly the principal."""
    extras = [Extra(date(2021, 5, 1), D("12345.67"), mode), Extra(date(2030, 7, 15), D("7000"), mode)]
    s = schedule(loan(principal=D("187654.32"), rate=D("2.17"), term_months=300), extras, date(2019, 1, 1))
    assert sum(r.principal for r in s.rows) + sum(p.applied for p in s.prepayments) == D("187654.32")
    assert s.rows[-1].outstanding == 0 and all(r.principal > 0 for r in s.rows)
