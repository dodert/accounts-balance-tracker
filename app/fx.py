"""Exchange rates, to add up balances in different currencies.

Rates come from the European Central Bank through the Frankfurter API (free, no key, also for past
days; the ECB publishes them once per working day, around 16:00 CET). They are kept in a store, one per
pair and day: the `fx_rates` table of the database (single user), or the shared DATA_DIR/rates.db
(multi-user, see SharedRates):
- a past day never changes, so it is fetched once;
- today's rate is fetched the first time it is needed each day, again when it is more than 3 hours old (until the
  ECB has published the day's own rate), and when asked (refresh).
Every conversion of the app (the combined total and the chart of every currency in the default one) uses
today's rate. If the service cannot be reached, the last stored rate is used, marked as stale.
Only currencies and dates are ever sent, never amounts.
"""
import json
import logging
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FxRate

log = logging.getLogger(__name__)

# Today's rate is asked again when the stored one is older than this (until the ECB publishes the day's own rate)
REFRESH = timedelta(hours=3)

# (base, quote, day or None for the latest) -> (rate, the day the rate is from)
Fetcher = Callable[[str, str, date | None], tuple[Decimal, date]]


def frankfurter(url: str) -> Fetcher:
    def fetch(base: str, quote: str, day: date | None) -> tuple[Decimal, date]:
        query = urllib.parse.urlencode({"from": base, "to": quote})
        request = urllib.request.Request(f"{url.rstrip('/')}/{day.isoformat() if day else 'latest'}?{query}",
                                         headers={"User-Agent": "accounts-balance-tracker"})
        with urllib.request.urlopen(request, timeout=10) as response:
            data = json.load(response)
        rate, rate_date = Decimal(str(data["rates"][quote])), date.fromisoformat(data["date"])
        log.info("Exchange rate %s/%s for %s: %s, asked to the service (ECB %s)", base, quote, day or "today",
                 rate, rate_date)
        return rate, rate_date
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


class DbStore:
    """The rates kept in the `fx_rates` table of a database (single user, and the demos)."""

    def __init__(self, db: Session):
        self.db = db

    def stored(self, base: str, quote: str) -> dict[date, Rate]:
        return {r.day: to_rate(r) for r in self.db.scalars(select(FxRate).where(FxRate.base == base, FxRate.quote == quote))}

    def save(self, rates: list[Rate]) -> None:
        """Store these rates, replacing the ones of the same pair and day, in one commit."""
        if not rates:
            return
        for rate in rates:
            row = self.db.scalar(select(FxRate).where(FxRate.base == rate.base, FxRate.quote == rate.quote,
                                                      FxRate.day == rate.day))
            if row is None:
                row = FxRate(base=rate.base, quote=rate.quote, day=rate.day)
                self.db.add(row)
            row.rate, row.rate_date, row.fetched_at = rate.rate, rate.rate_date, rate.fetched_at
        self.db.commit()


class SharedStore:
    """Multi-user: the rates of the shared store, never written to the user's (encrypted) database. Rates that
    database already has (from older versions, or a demo's sample rate) are still used when the shared store
    has none for that day."""

    def __init__(self, shared: "SharedRates", fallback: list[Rate] = ()):
        self.shared, self.fallback = shared, list(fallback)

    def stored(self, base: str, quote: str) -> dict[date, Rate]:
        out = {r.day: r for r in self.fallback if r.base == base and r.quote == quote}
        out.update(self.shared.stored(base, quote))
        return out

    def save(self, rates: list[Rate]) -> None:
        pass  # the shared fetcher has stored them already


def own_rates(db: Session) -> list[Rate]:
    """Every rate kept in a database's `fx_rates` table."""
    return [to_rate(r) for r in db.scalars(select(FxRate))]


def get_rate(store, fetch: Fetcher, base: str, quote: str, day: date | None = None, refresh: bool = False) -> Rate:
    """The rate to convert `base` into `quote` on `day` (default today). Raises LookupError when there
    is no stored rate for the pair and the service cannot be reached."""
    base, quote = base.upper(), quote.upper()
    today = date.today()
    day = min(day or today, today)
    if base == quote:
        return Rate(base, quote, day, Decimal(1), day, datetime.now())
    rows = store.stored(base, quote)
    row = rows.get(day)
    if is_fresh(row, today) and not refresh:
        log.info("Exchange rate %s/%s for %s: %s, from the stored rates (ECB %s)", base, quote, day, row.rate,
                 row.rate_date)
        return row
    try:
        value, rate_date = fetch(base, quote, None if day == today else day)
    except Exception as exc:  # network, HTTP or format error: fall back to what is stored
        log.warning("Could not fetch the %s/%s rate for %s: %s", base, quote, day, exc)
        last = row or (rows[max(rows)] if rows else None)
        if last is None:
            raise LookupError(f"No exchange rate for {base}/{quote}: {exc}") from None
        log.warning("Exchange rate %s/%s for %s: %s, the last stored one (ECB %s)", base, quote, day, last.rate,
                    last.rate_date)
        return replace(last, stale=True)
    rate = Rate(base, quote, day, value, rate_date, datetime.now())
    store.save([rate])
    return rate


def is_fresh(row: Rate | None, today: date, now: datetime | None = None) -> bool:
    """A stored rate needs no new request: a past day never changes. Today's is asked again when it is more than
    REFRESH old (without pressing the refresh button), unless it is already the day's own rate, which the ECB
    publishes once (around 16:00 CET) and never changes."""
    if row is None:
        return False
    if row.day < today:
        return True
    now = now or datetime.now()
    return row.fetched_at.date() == today and (row.rate_date >= today or now - row.fetched_at < REFRESH)


def to_rate(row: FxRate, stale: bool = False) -> Rate:
    return Rate(row.base, row.quote, row.day, Decimal(row.rate), row.rate_date, row.fetched_at, stale)


class SharedRates:
    """Multi-user mode: one store of exchange rates for every user and every demo (a rate for a pair of currencies
    on a day is the same for everyone), in DATA_DIR/rates.db: only currencies, dates and rates, never amounts.

    Each rate is asked to the service once for everybody, and at most `daily_limit` requests a day are made
    (counted in the file, so restarts don't reset it); past it the request fails and the rates already stored
    are used. A past day never changes. Today's rate is asked again at most once every LATEST_SECONDS, and not
    at all once the day's own rate (published by the ECB around 16:00 CET) is stored."""

    LATEST_SECONDS = REFRESH.total_seconds()

    def __init__(self, path: Path, fetch: Fetcher, daily_limit: int, clock=time.time, today=date.today):
        self._fetch, self.daily_limit, self._clock, self._today = fetch, daily_limit, clock, today
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS rates (base TEXT, quote TEXT, day TEXT, rate TEXT, rate_date TEXT,"
            " fetched_at REAL, fetched_on TEXT, PRIMARY KEY (base, quote, day));"
            "CREATE TABLE IF NOT EXISTS requests (day TEXT PRIMARY KEY, count INTEGER)")
        self._db.commit()

    def requests_today(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT count FROM requests WHERE day = ?", (self._today().isoformat(),)).fetchone()
        return row[0] if row else 0

    def _stored(self, base: str, quote: str, day: date | None) -> tuple[Decimal, date] | None:
        key = self._key(day)
        row = self._db.execute("SELECT rate, rate_date, fetched_at, fetched_on FROM rates "
                               "WHERE base = ? AND quote = ? AND day = ?",
                               (base, quote, key)).fetchone()
        if row is None:
            return None
        rate, rate_date = Decimal(row[0]), date.fromisoformat(row[1])
        if key == "latest":  # today's: from today, and either the day's own rate or asked less than an hour ago
            today = self._today()
            if row[3] != today.isoformat() or (rate_date != today and self._clock() - row[2] > self.LATEST_SECONDS):
                return None
        return rate, rate_date

    def _take_request(self) -> bool:
        today = self._today().isoformat()
        row = self._db.execute("SELECT count FROM requests WHERE day = ?", (today,)).fetchone()
        if (row[0] if row else 0) >= self.daily_limit:
            return False
        self._db.execute("INSERT INTO requests (day, count) VALUES (?, 1) "
                         "ON CONFLICT(day) DO UPDATE SET count = count + 1", (today,))
        self._db.execute("DELETE FROM requests WHERE day < ?", ((self._today() - timedelta(days=7)).isoformat(),))
        self._db.commit()
        return True

    def _key(self, day: date | None) -> str:
        return "latest" if day is None or day >= self._today() else day.isoformat()

    def _store(self, base: str, quote: str, day: date | None, rate: Decimal, rate_date: date) -> None:
        self._db.execute("INSERT OR REPLACE INTO rates VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (base, quote, self._key(day), str(rate), rate_date.isoformat(), self._clock(),
                          self._today().isoformat()))

    def stored(self, base: str, quote: str) -> dict[date, Rate]:
        """Every rate kept for the pair, by the day it was asked for (today's rate of an earlier day counts as that
        day's), whatever its age."""
        with self._lock:
            rows = self._db.execute("SELECT day, rate, rate_date, fetched_at, fetched_on FROM rates "
                                    "WHERE base = ? AND quote = ?", (base, quote)).fetchall()
        out = {}
        for day, rate, rate_date, fetched_at, fetched_on in rows:
            asked = date.fromisoformat(fetched_on if day == "latest" else day)
            out[asked] = Rate(base, quote, asked, Decimal(rate), date.fromisoformat(rate_date),
                              datetime.fromtimestamp(fetched_at))
        return out

    def fetcher(self, network: bool = True) -> Fetcher:
        """A Fetcher answered from the shared store. With network=False (demos) the service is never asked."""
        def fetch(base: str, quote: str, day: date | None) -> tuple[Decimal, date]:
            with self._lock:
                stored = self._stored(base, quote, day)
                if stored is not None:
                    log.info("Exchange rate %s/%s for %s: %s, from the shared store (ECB %s)", base, quote,
                             day or "today", *stored)
                    return stored
                if not network:
                    raise LookupError("not stored: demos use only stored exchange rates")
                if not self._take_request():
                    raise RuntimeError(f"daily limit of {self.daily_limit} requests to the rate service reached")
            rate, rate_date = self._fetch(base, quote, day)
            with self._lock:
                self._store(base, quote, day, rate, rate_date)
                self._db.commit()
            return rate, rate_date

        return fetch
