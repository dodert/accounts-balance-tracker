"""Totals, history and exchange rates."""
from contextlib import contextmanager
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import Context
from ..fx import DbStore, SharedStore, get_rate, own_rates
from ..history import available_totals, currency_totals, history, load
from ..models import Account
from ..schemas import CombinedTotal, CurrencyTotal, FxRateOut, HistoryOut


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings, multi, fetch_rate, shared_rates, get_db, open_db, in_demo = (
        ctx.settings, ctx.multi, ctx.fetch_rate, ctx.shared_rates, ctx.get_db, ctx.open_db, ctx.in_demo)

    # --- Summary and history ---

    @router.get("/api/summary", response_model=list[CurrencyTotal])
    def summary(db: Session = Depends(get_db)):
        return currency_totals(load(db))

    def multi_fallback(request: Request) -> list:
        """Multi-user: the rates the user's (or demo's) own database already has, used only when the shared store
        has none for a day. The user's database is opened and closed here: it is never open while the rate service
        is asked, so the user's other requests don't wait for it, and rates are never written to it."""
        if not multi:
            return []
        with open_db(request) as db:
            return own_rates(db)

    @contextmanager
    def rate_source(request: Request, fallback: list):
        """(store, fetcher) of the rates. Single user: the database's own table and the service. Multi-user: the
        store shared by everybody, which demos only read (a demo never makes the app ask the service)."""
        if multi:
            yield SharedStore(shared_rates, fallback), shared_rates.fetcher(network=not in_demo(request))
            return
        with open_db(request) as db:
            yield DbStore(db), fetch_rate

    def combined_total(request: Request, totals: list[dict], fallback: list, refresh: bool) -> CombinedTotal:
        target = settings.default_currency
        total = available = Decimal(0)
        rates, missing = [], []
        with rate_source(request, fallback) as (store, fetch):
            for c in totals:
                try:
                    rate = get_rate(store, fetch, c["currency"], target, refresh=refresh)
                except LookupError:
                    missing.append(c["currency"])
                    continue
                total += c["total"] * rate.rate
                available += c["available"] * rate.rate
                if c["currency"] != target:
                    rates.append(FxRateOut(**vars(rate)))
        # Rounded to the cent (half up) only when sent: schemas.Cents
        return CombinedTotal(currency=target, total=total, available=available, rates=rates, missing=missing)

    def totals_and_rates(request: Request) -> tuple[list[dict], list]:
        with open_db(request) as db:
            return currency_totals(load(db)), (own_rates(db) if multi else [])

    @router.get("/api/summary/combined", response_model=CombinedTotal)
    def summary_combined(request: Request):
        """All currencies in the default one. Today's rates are fetched the first time each day."""
        return combined_total(request, *totals_and_rates(request), refresh=False)

    @router.post("/api/fx/refresh", response_model=CombinedTotal)
    def fx_refresh(request: Request):
        """Fetch today's rates again now, and the combined total with them."""
        return combined_total(request, *totals_and_rates(request), refresh=True)

    @router.get("/api/fx", response_model=FxRateOut)
    def fx_rate(
        request: Request,
        base: str = Query(..., alias="from", pattern=r"^[A-Za-z]{3}$"),
        quote: str = Query(..., alias="to", pattern=r"^[A-Za-z]{3}$"),
        day: date | None = Query(None, alias="date"),
    ):
        """The rate from one currency to another on a day (default today)."""
        try:
            with rate_source(request, multi_fallback(request)) as (store, fetch):
                return FxRateOut(**vars(get_rate(store, fetch, base, quote, day)))
        except LookupError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/api/currencies", response_model=list[str])
    def currencies(db: Session = Depends(get_db)):
        return db.scalars(select(Account.currency).distinct().order_by(Account.currency)).all()

    @router.get("/api/history", response_model=HistoryOut)
    def get_history(
        currency: str | None = Query(None, pattern=r"^[A-Za-z]{3}$"),
        since: date | None = None,
        db: Session = Depends(get_db),
    ):
        currency = (currency or "").upper() or db.scalar(
            select(Account.currency).group_by(Account.currency).order_by(func.count().desc(), Account.currency).limit(1)
        ) or settings.default_currency
        ledger = load(db, currency)
        accounts = ledger.accounts
        dates, totals, per_account = history(ledger, since)
        return HistoryOut(
            currency=currency,
            dates=dates,
            total=totals,
            available=available_totals(accounts, dates, per_account),
            accounts=[
                {"account_id": a.id, "name": a.name, "archived": a.archived,
                 "values": per_account[a.id]}
                for a in accounts
            ],
        )

    @router.get("/api/history/converted", response_model=HistoryOut)
    def get_history_converted(request: Request, since: date | None = None):
        """Every account in the default currency at TODAY's rate, on every date: what the balances are worth now,
        without the changes of the exchange rate. Only the currencies are sent to the rate service; the amounts
        are converted here."""
        target = settings.default_currency
        with open_db(request) as db:
            ledger = load(db)
            fallback = own_rates(db) if multi else []
        accounts = ledger.accounts
        all_dates, _, all_values = history(ledger)
        dates, _, values = history(ledger, since)
        rates, converted, missing, stale = {}, [], [], False
        with rate_source(request, fallback) as (store, fetch):
            for currency in sorted({a.currency for a in accounts} - {target}):
                try:
                    rate = get_rate(store, fetch, currency, target)
                except LookupError:
                    missing.append(currency)
                    continue
                rates[currency] = rate.rate
                converted.append(currency)
                stale = stale or rate.stale
        used = [a for a in accounts if a.currency == target or a.currency in rates]

        def convert(account, amounts):
            if account.currency == target:
                return amounts
            return [None if v is None else v * rates[account.currency] for v in amounts]

        def totals(per_account, days):
            total = [sum((v[n] for v in per_account.values() if v[n] is not None), Decimal(0)) for n in range(len(days))]
            return total, available_totals(used, days, per_account)

        per_account = {a.id: convert(a, values[a.id]) for a in used}
        total, available = totals(per_account, dates)
        all_per_account = {a.id: convert(a, all_values[a.id]) for a in used}
        all_total, all_available = totals(all_per_account, all_dates)
        money = lambda v: v  # noqa: E731  (rounded to the cent when sent: schemas.Cents)
        return HistoryOut(
            currency=target, dates=dates, total=[money(v) for v in total], available=[money(v) for v in available],
            accounts=[
                {"account_id": a.id, "name": a.name, "archived": a.archived, "currency": a.currency,
                 "start": money(next((v for v in all_per_account[a.id] if v is not None), None)),
                 "values": [money(v) for v in per_account[a.id]]}
                for a in used
            ],
            first_total=money(all_total[0]) if len(all_dates) > 1 else None,
            first_available=money(all_available[0]) if len(all_dates) > 1 else None,
            converted=converted, missing=missing, stale=stale,
        )

    return router
