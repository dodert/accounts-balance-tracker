"""Totals and history built from the recorded balances.

An account's balance on a day is the last one recorded on or before that day (it stays the same
until a new one is recorded). The total of a currency on a day is the sum of those balances for
every account in that currency, archived ones included: close an account by recording a 0
balance, then archive it, and the history stays right.

The balances are read as plain (date, amount) rows, never as ORM objects: with a hundred thousand of
them, building the objects was most of the time of each request.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Account, Balance

Series = list[tuple[date, Decimal]]  # an account's balances, oldest first


@dataclass
class Ledger:
    accounts: list[Account]  # by name; their `balances` relationship is not loaded
    series: dict[int, Series]  # account id -> its balances


def load(db: Session, currency: str | None = None) -> Ledger:
    """The accounts (of one currency, or all) and their balances."""
    accounts_stmt = select(Account).order_by(Account.name)
    balances_stmt = select(Balance.account_id, Balance.date, Balance.amount).order_by(Balance.account_id, Balance.date)
    if currency:
        accounts_stmt = accounts_stmt.where(Account.currency == currency)
        balances_stmt = balances_stmt.join(Account).where(Account.currency == currency)
    accounts = list(db.scalars(accounts_stmt))
    series: dict[int, Series] = {a.id: [] for a in accounts}
    for account_id, day, amount in db.execute(balances_stmt):
        series[account_id].append((day, amount))
    return Ledger(accounts, series)


def subset(ledger: Ledger, accounts: list[Account]) -> Ledger:
    return Ledger(accounts, {a.id: ledger.series[a.id] for a in accounts})


def latest_balances(db: Session, account_ids: list[int] | None = None) -> dict[int, dict]:
    """Per account: its last, previous and first balance (date, amount), in one query."""
    newest = func.row_number().over(partition_by=Balance.account_id, order_by=Balance.date.desc()).label("newest")
    oldest = func.row_number().over(partition_by=Balance.account_id, order_by=Balance.date).label("oldest")
    inner = select(Balance.account_id, Balance.date, Balance.amount, newest, oldest)
    if account_ids is not None:
        inner = inner.where(Balance.account_id.in_(account_ids))
    ranked = inner.subquery()
    rows = db.execute(select(ranked).where((ranked.c.newest <= 2) | (ranked.c.oldest == 1)))
    out: dict[int, dict] = {}
    for account_id, day, amount, n_newest, n_oldest in rows:
        found = out.setdefault(account_id, {})
        if n_newest == 1:
            found["last"] = (day, amount)
        if n_newest == 2:
            found["previous"] = (day, amount)
        if n_oldest == 1:
            found["first"] = (day, amount)
    return out


def history(ledger: Ledger, since: date | None = None) -> tuple[list[date], list[Decimal], dict[int, list[Decimal | None]]]:
    """(dates, total per date, values per account id) over every date with a recorded balance.

    With `since`, dates before it are dropped, but balances recorded before it still count
    (an account last updated before `since` keeps its balance)."""
    all_dates = sorted({day for a in ledger.accounts for day, _ in ledger.series[a.id]})
    dates = [d for d in all_dates if since is None or d >= since]
    if since is not None and (not dates or dates[0] > since) and any(d < since for d in all_dates):
        dates.insert(0, since)  # the starting point of the range
    per_account: dict[int, list[Decimal | None]] = {}
    for account in ledger.accounts:
        values, current, balances, i = [], None, ledger.series[account.id], 0
        for day in dates:
            while i < len(balances) and balances[i][0] <= day:
                current = balances[i][1]
                i += 1
            values.append(current)
        per_account[account.id] = values
    totals = [sum((v[n] for v in per_account.values() if v[n] is not None), Decimal(0)) for n in range(len(dates))]
    return dates, totals, per_account


def is_available(account: Account, day: date) -> bool:
    return account.available_from is None or account.available_from <= day


def available_totals(accounts: list[Account], dates: list[date], per_account: dict[int, list[Decimal | None]]) -> list[Decimal]:
    """Per date, the sum of the balances of the accounts already available on that date."""
    return [
        sum((per_account[a.id][n] for a in accounts if per_account[a.id][n] is not None and is_available(a, day)), Decimal(0))
        for n, day in enumerate(dates)
    ]


def currency_totals(ledger: Ledger) -> list[dict]:
    """Current total per currency, and the totals on the previous and the first recorded dates."""
    out = []
    for currency in sorted({a.currency for a in ledger.accounts}):
        group = [a for a in ledger.accounts if a.currency == currency]
        dates, totals, per_account = history(subset(ledger, group))
        today = date.today()
        available = sum((v[-1] for a in group if (v := per_account[a.id]) and v[-1] is not None and is_available(a, today)), Decimal(0))
        out.append({
            "currency": currency,
            "accounts": sum(1 for a in group if not a.archived),
            "total": float(totals[-1]) if totals else 0.0,
            "date": dates[-1] if dates else None,
            "previous_total": float(totals[-2]) if len(totals) > 1 else None,
            "previous_date": dates[-2] if len(dates) > 1 else None,
            "first_total": float(totals[0]) if len(totals) > 1 else None,
            "first_date": dates[0] if len(dates) > 1 else None,
            "available": float(available),
            "first_available": float(available_totals(group, dates[:1], per_account)[0]) if len(dates) > 1 else None,
        })
    return out
