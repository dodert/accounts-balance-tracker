"""Exchange rates, to add up balances in different currencies.

Rates come from the European Central Bank through the Frankfurter API (free, no key, also for past
days; the ECB publishes them once per working day, around 16:00 CET). They are kept in a store, one per
pair and day: the `fx_rates` table of the database (single user), or the shared DATA_DIR/rates.db
(multi-user, see SharedRates):
- a past day never changes, so it is fetched once;
- today's rate is fetched the first time it is needed each day, and again only when asked (refresh);
- the rates of many days (a converted history) are asked in ONE request for the whole range.
If the service cannot be reached, the stored rate nearest in time is used, marked as stale.
Only currencies and dates are ever sent, never amounts.
"""
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import FxRate

log = logging.getLogger(__name__)

# (base, quote, day or None for the latest) -> (rate, the day the rate is from)
Fetcher = Callable[[str, str, date | None], tuple[Decimal, date]]
# A fetcher may also have `days(base, quote, set of days) -> {day: (rate, the day the rate is from)}`, which asks
# for all of them at once (see frankfurter); without it, each day is asked on its own
RANGE_MARGIN = timedelta(days=7)  # a range starts this much earlier, so its first day has a working day before it


class RangeUnsupported(Exception):
    """The service did not understand a request for a range of days: ask day by day instead."""


def frankfurter(url: str) -> Fetcher:
    base_url = url.rstrip("/")

    def get(path: str, base: str, quote: str) -> dict:
        query = urllib.parse.urlencode({"from": base, "to": quote})
        request = urllib.request.Request(f"{base_url}/{path}?{query}", headers={"User-Agent": "accounts-balance-tracker"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def fetch(base: str, quote: str, day: date | None) -> tuple[Decimal, date]:
        data = get(day.isoformat() if day else "latest", base, quote)
        return Decimal(str(data["rates"][quote])), date.fromisoformat(data["date"])

    def days(base: str, quote: str, wanted: set[date]) -> dict[date, tuple[Decimal, date]]:
        """Every day of `wanted` in one request (a time series from a week before the first to the last)."""
        start, end = min(wanted) - RANGE_MARGIN, max(wanted)
        try:
            data = get(f"{start.isoformat()}..{end.isoformat()}", base, quote)
            published = sorted((date.fromisoformat(day), Decimal(str(rates[quote])))
                               for day, rates in data["rates"].items())
        except urllib.error.HTTPError as error:
            if error.code in (400, 404, 422):
                raise RangeUnsupported(f"HTTP {error.code}") from None
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise RangeUnsupported(f"unexpected answer: {error!r}") from None
        return resolve(published, wanted)

    fetch.days = days
    return fetch


def resolve(published: list[tuple[date, Decimal]], wanted) -> dict[date, tuple[Decimal, date]]:
    """For each wanted day, the rate of the last working day on or before it (rates are published on working days
    only); days with none before them are left out."""
    published_days = [day for day, _ in published]
    out = {}
    for day in wanted:
        i = bisect_right(published_days, day)
        if i:
            out[day] = (published[i - 1][1], published_days[i - 1])
    return out


def fetch_days(fetch: Fetcher, base: str, quote: str, wanted: set[date],
               take: Callable[[], bool] = lambda: True) -> dict[date, tuple[Decimal, date]]:
    """The rates of `wanted` days: in one request if the fetcher can, else one request per day. `take` is called
    before each request and False stops (a daily limit). Day by day, the first failure stops too; what was
    fetched until then is returned (the caller uses the nearest stored rate for the rest), or the error raised
    if nothing was."""
    if not wanted:
        return {}
    days = getattr(fetch, "days", None)
    if days is not None:
        if not take():
            raise RuntimeError("daily limit of requests to the rate service reached")
        try:
            return days(base, quote, set(wanted))
        except RangeUnsupported as error:
            log.warning("The rate service did not answer a range of days (%s): asking day by day", error)
    today = date.today()
    out = {}
    for day in sorted(wanted):
        try:
            if not take():
                raise RuntimeError("daily limit of requests to the rate service reached")
            out[day] = fetch(base, quote, None if day >= today else day)
        except Exception as error:
            if not out:
                raise
            log.warning("Could not fetch the %s/%s rate for %s: %s", base, quote, day, error)
            break
    return out


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
        return row
    try:
        value, rate_date = fetch(base, quote, None if day == today else day)
    except Exception as exc:  # network, HTTP or format error: fall back to what is stored
        log.warning("Could not fetch the %s/%s rate for %s: %s", base, quote, day, exc)
        last = row or (rows[max(rows)] if rows else None)
        if last is None:
            raise LookupError(f"No exchange rate for {base}/{quote}: {exc}") from None
        return replace(last, stale=True)
    rate = Rate(base, quote, day, value, rate_date, datetime.now())
    store.save([rate])
    return rate


def is_fresh(row: Rate | None, today: date) -> bool:
    """A stored rate needs no new request: a past day never changes, today's is fetched once a day."""
    return row is not None and (row.day < today or row.fetched_at.date() == today)


def rates_for_days(store, fetch: Fetcher, base: str, quote: str, days) -> tuple[dict[date, Decimal], bool]:
    """The rate on each of `days` (to draw a history converted into another currency), and whether
    some of them are not that day's own rate (the service could not be reached).

    Stored rates are used; the others are asked for in one request (see fetch_days), and kept. Days
    still without a rate take the stored rate nearest to them. Raises LookupError when nothing is
    stored for the pair and the service cannot be reached."""
    base, quote = base.upper(), quote.upper()
    today = date.today()
    if base == quote:
        return {d: Decimal(1) for d in days}, False
    rows = store.stored(base, quote)
    wanted = {min(d, today) for d in days}
    missing = {day for day in wanted if not is_fresh(rows.get(day), today)}
    if missing:
        try:
            fetched = fetch_days(fetch, base, quote, missing)
        except Exception as exc:
            log.warning("Could not fetch the %s/%s rates for %d days: %s", base, quote, len(missing), exc)
            fetched = {}
        now = datetime.now()
        new = [Rate(base, quote, day, value, rate_date, now) for day, (value, rate_date) in fetched.items()]
        rows.update({rate.day: rate for rate in new})
        store.save(new)
    out: dict[date, Decimal] = {}
    pending: list[date] = []
    old = False
    for d in sorted(set(days)):
        row = rows.get(min(d, today))
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


class SharedRates:
    """Multi-user mode: one store of exchange rates for every user and every demo (a rate for a pair of currencies
    on a day is the same for everyone), in DATA_DIR/rates.db: only currencies, dates and rates, never amounts.

    Each rate is asked to the service once for everybody, and at most `daily_limit` requests a day are made
    (counted in the file, so restarts don't reset it); past it the request fails and the rates already stored
    are used. A past day never changes. Today's rate is asked again at most once every LATEST_SECONDS, and not
    at all once the day's own rate (published by the ECB around 16:00 CET) is stored."""

    LATEST_SECONDS = 3600

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

    def _take_request_locked(self) -> bool:
        with self._lock:
            return self._take_request()

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

        def days(base: str, quote: str, wanted: set[date]) -> dict[date, tuple[Decimal, date]]:
            """The stored ones, and the rest in one request to the service (one of the daily limit)."""
            today = self._today()
            out, missing = {}, set()
            with self._lock:
                for day in wanted:
                    stored = self._stored(base, quote, None if day >= today else day)
                    if stored is not None:
                        out[day] = stored
                    else:
                        missing.add(day)
            if not missing or not network:
                return out
            try:
                fetched = fetch_days(self._fetch, base, quote, missing, take=self._take_request_locked)
            except Exception as error:
                if not out:
                    raise
                log.warning("Could not fetch the %s/%s rates: %s", base, quote, error)
                return out
            with self._lock:
                for day, (rate, rate_date) in fetched.items():
                    self._store(base, quote, day, rate, rate_date)
                self._db.commit()
            return out | fetched

        fetch.days = days
        return fetch
