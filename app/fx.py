"""Exchange rates, to add up balances in different currencies.

Rates come from the European Central Bank through the Frankfurter API (free, no key, also for past
days; the ECB publishes them once per working day, around 16:00 CET). They are stored in `fx_rates`,
one row per pair and day:
- a past day never changes, so it is fetched once;
- today's rate is fetched the first time it is needed each day, and again only when asked (refresh).
If the service cannot be reached, the last stored rate for the pair is used, marked as stale.
"""
import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FxRate

log = logging.getLogger(__name__)

# (base, quote, day or None for the latest) -> (rate, the day the rate is from)
Fetcher = Callable[[str, str, date | None], tuple[Decimal, date]]


def frankfurter(url: str) -> Fetcher:
    def fetch(base: str, quote: str, day: date | None) -> tuple[Decimal, date]:
        query = urllib.parse.urlencode({"from": base, "to": quote})
        request = urllib.request.Request(f"{url.rstrip('/')}/{day.isoformat() if day else 'latest'}?{query}",
                                         headers={"User-Agent": "accounts-balance-tracker"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        return Decimal(str(data["rates"][quote])), date.fromisoformat(data["date"])
    return fetch


@dataclass
class Rate:
    base: str
    quote: str
    day: date          # the day it was asked for
    rate: Decimal
    rate_date: date    # the day the rate is from (the previous working day on weekends and holidays)
    fetched_at: datetime
    stale: bool = False  # the service could not be reached: an older stored rate


def get_rate(db: Session, fetch: Fetcher, base: str, quote: str, day: date | None = None, refresh: bool = False) -> Rate:
    """The rate to convert `base` into `quote` on `day` (default today). Raises LookupError when there
    is no stored rate for the pair and the service cannot be reached."""
    base, quote = base.upper(), quote.upper()
    today = date.today()
    day = min(day or today, today)
    if base == quote:
        now = datetime.now()
        return Rate(base, quote, day, Decimal(1), day, now)
    row = db.scalar(select(FxRate).where(FxRate.base == base, FxRate.quote == quote, FxRate.day == day))
    if is_fresh(row, today) and not refresh:
        return to_rate(row)
    try:
        return to_rate(fetch_and_store(db, fetch, base, quote, day, row))
    except Exception as exc:  # network, HTTP or format error: fall back to what is stored
        log.warning("Could not fetch the %s/%s rate for %s: %s", base, quote, day, exc)
        last = row or db.scalar(select(FxRate).where(FxRate.base == base, FxRate.quote == quote)
                                .order_by(FxRate.day.desc()).limit(1))
        if last is None:
            raise LookupError(f"No exchange rate for {base}/{quote}: {exc}") from None
        return to_rate(last, stale=True)


def is_fresh(row: FxRate | None, today: date) -> bool:
    """A stored rate needs no new request: a past day never changes, today's is fetched once a day."""
    return row is not None and (row.day < today or row.fetched_at.date() == today)


def fetch_and_store(db: Session, fetch: Fetcher, base: str, quote: str, day: date, row: FxRate | None) -> FxRate:
    """Ask the service for the rate on `day` and store it. Raises whatever the fetcher raises."""
    value, rate_date = fetch(base, quote, None if day == date.today() else day)
    if row is None:
        row = FxRate(base=base, quote=quote, day=day)
        db.add(row)
    row.rate, row.rate_date, row.fetched_at = value, rate_date, datetime.now()
    db.commit()
    return row


def rates_for_days(db: Session, fetch: Fetcher, base: str, quote: str, days) -> tuple[dict[date, Decimal], bool]:
    """The rate on each of `days` (to draw a history converted into another currency), and whether
    some of them are not that day's own rate (the service could not be reached).

    Stored rates are used; the others are asked for one by one, and kept. After the first failure
    the service is not asked again in this call, and those days take the stored rate nearest to
    them. Raises LookupError when nothing is stored for the pair and the service cannot be reached."""
    base, quote = base.upper(), quote.upper()
    today = date.today()
    if base == quote:
        return {d: Decimal(1) for d in days}, False
    rows = {r.day: r for r in db.scalars(select(FxRate).where(FxRate.base == base, FxRate.quote == quote))}
    out: dict[date, Decimal] = {}
    pending: list[date] = []
    down = old = False
    for d in sorted(set(days)):
        day = min(d, today)
        row = rows.get(day)
        if not is_fresh(row, today) and not down:
            try:
                row = rows[day] = fetch_and_store(db, fetch, base, quote, day, row)
            except Exception as exc:
                log.warning("Could not fetch the %s/%s rate for %s: %s", base, quote, day, exc)
                down = True
        if row is None:
            pending.append(d)
            continue
        out[d] = Decimal(row.rate)
        old = old or not is_fresh(row, today)  # today's rate from an earlier day: the service is down
    if pending:
        if not rows:
            raise LookupError(f"No exchange rate for {base}/{quote}")
        for d in pending:
            nearest = min(rows, key=lambda r: abs((r - d).days))
            out[d] = Decimal(rows[nearest].rate)
    return out, old or bool(pending)


def to_rate(row: FxRate, stale: bool = False) -> Rate:
    return Rate(row.base, row.quote, row.day, Decimal(row.rate), row.rate_date, row.fetched_at, stale)
