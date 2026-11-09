"""Demo mode: the app with made-up sample data, to show it to other people or to try it while it is empty.

The sample data lives in its own SQLite file in a temporary folder, never in the data folder, and is
reached only by browsers that have the demo cookie: demo mode never reads, changes or shows the real
data. The file is deleted when the app stops, and the data is built again (relative to today) on reset.
"""
import random
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.orm import Session

from .db import Base, make_engine, make_sessionmaker
from .models import Account, AccountType, Balance, FxRate, Milestone

COOKIE = "abt_demo"
# Most size of a demo database: anyone can try the demo, so its files (in the temporary folder) can't grow without end
MAX_BYTES = 5 * 1024 * 1024
MONTHS = 24


@dataclass
class Sample:
    name: dict[str, str]          # by language
    institution: str | None
    type: AccountType
    currency: str
    start: float                  # balance on the first month it has
    monthly: float                # average change per month
    noise: float                  # random change per month (plus or minus)
    first_month: int = 0          # months after the start of the history it was opened
    available_in_days: int | None = None  # locked until then (e.g. unvested stock options)
    closed_month: int | None = None       # closed with a 0 balance and archived
    note: dict[str, str] | None = None
    # Notable expenses: (months after the start of the history, amount, description by language)
    milestones: tuple = ()


SAMPLES = [
    Sample({"en": "Current account", "es": "Cuenta corriente"}, "Banco Aurora", AccountType.bank, "EUR", 2400, 30, 300,
           milestones=((15, 1800, {"en": "Summer holidays", "es": "Vacaciones de verano"}),)),
    Sample({"en": "Savings", "es": "Ahorro"}, "Banco Aurora", AccountType.savings, "EUR", 18000, 400, 60,
           milestones=((9, 12500, {"en": "New car", "es": "Coche nuevo"}),)),
    Sample({"en": "Credit card", "es": "Tarjeta de crédito"}, "Banco Aurora", AccountType.loan, "EUR", -450, 0, 300),
    Sample({"en": "Salary account", "es": "Cuenta nómina"}, "Caja Litoral", AccountType.bank, "EUR", 2600, 40, 250),
    Sample({"en": "Mortgage", "es": "Hipoteca"}, "Caja Litoral", AccountType.loan, "EUR", -96000, 480, 0),
    Sample({"en": "Term deposit", "es": "Depósito a plazo"}, "Caja Litoral", AccountType.savings, "EUR", 10000, 0, 0,
           closed_month=14, note={"en": "Matured", "es": "Vencido"}),
    Sample({"en": "Index fund", "es": "Fondo indexado"}, "Northwind Brokers", AccountType.investment, "EUR", 32000, 600, 1200),
    Sample({"en": "US stocks", "es": "Acciones EE. UU."}, "Northwind Brokers", AccountType.investment, "USD", 9000, 250, 700),
    Sample({"en": "Stock options", "es": "Stock options"}, "Northwind Brokers", AccountType.investment, "USD", 4000, 250, 600,
           first_month=6, available_in_days=400, note={"en": "Vesting", "es": "Pendiente de consolidar"}),
    Sample({"en": "Checking", "es": "Cuenta en dólares"}, "Liberty Bank", AccountType.bank, "USD", 1500, 30, 250),
    Sample({"en": "Pension plan", "es": "Plan de pensiones"}, "Seguros Atlántico", AccountType.pension, "EUR", 34000, 300, 400),
    Sample({"en": "Bitcoin", "es": "Bitcoin"}, "Coinvault", AccountType.crypto, "EUR", 1200, 60, 400, first_month=8),
    Sample({"en": "Cash", "es": "Efectivo"}, None, AccountType.cash, "EUR", 300, 0, 80),
]


def month_starts(today: date, months: int = MONTHS) -> list[date]:
    """The first day of the last `months` months, today's month included, oldest first."""
    out, year, month = [], today.year, today.month
    for _ in range(months):
        out.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return out[::-1]


def seed(db: Session, lang: str = "en", today: date | None = None) -> None:
    """Fill an empty database with the sample accounts and their monthly balances."""
    today = today or date.today()
    lang = lang if lang in ("en", "es") else "en"
    rng = random.Random(42)  # the same data every time
    days = month_starts(today)
    for sample in SAMPLES:
        account = Account(
            name=sample.name[lang], institution=sample.institution, type=sample.type, currency=sample.currency,
            available_from=today + timedelta(days=sample.available_in_days) if sample.available_in_days else None,
            notes=sample.note[lang] if sample.note else None,
            archived=sample.closed_month is not None,
        )
        db.add(account)
        value = sample.start
        for n, day in enumerate(days):
            if n < sample.first_month:
                continue
            if sample.closed_month is not None and n > sample.closed_month:
                break
            closing = n == sample.closed_month
            amount = 0 if closing else value
            if sample.type == AccountType.loan and amount > 0:
                amount = 0
            account.balances.append(Balance(date=day, amount=Decimal(str(round(amount, 2)))))
            value += sample.monthly + rng.uniform(-sample.noise, sample.noise)
            for month, cost, description in sample.milestones:
                if month == n:  # the expense, a few days into the month, and the balance drops by it
                    account.milestones.append(Milestone(date=day + timedelta(days=12), amount=Decimal(cost), description=description[lang]))
                    value -= cost
    # A sample USD/EUR rate from yesterday, so the combined total works offline too: today's real
    # rate is still fetched (only the two currencies are sent) and replaces it when it can be
    yesterday = today - timedelta(days=1)
    db.add(FxRate(base="USD", quote="EUR", day=yesterday, rate=Decimal("0.92"), rate_date=yesterday,
                  fetched_at=datetime.combine(yesterday, datetime.min.time())))
    db.commit()


class Demo:
    """The demo database: created the first time it is used, deleted when the app stops."""

    def __init__(self, max_bytes: int | None = None):
        self.max_bytes = MAX_BYTES if max_bytes is None else max_bytes
        self._lock = threading.Lock()
        self._dir: Path | None = None
        self._engine = None
        self.Session = None

    def sessionmaker(self, lang: str = "en"):
        with self._lock:
            if self.Session is None:
                self._create(lang)
            return self.Session

    def reset(self, lang: str = "en") -> None:
        with self._lock:
            self._close()
            self._create(lang)

    def _create(self, lang: str) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="abt-demo-"))
        self._engine = make_engine(f"sqlite:///{self._dir / 'demo.db'}")
        if self.max_bytes:
            @event.listens_for(self._engine, "connect")
            def _limit_size(connection, _record):
                # Past this size SQLite refuses the write ("database or disk is full"): see main.py
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute(f"PRAGMA max_page_count = {self.max_bytes // page_size}")
        Base.metadata.create_all(self._engine)
        self.Session = make_sessionmaker(self._engine)
        with self.Session() as db:
            seed(db, lang)

    def _close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)
        self._dir = self._engine = self.Session = None

    def close(self) -> None:
        with self._lock:
            self._close()


class VisitorDemos:
    """One demo database per visitor (browser), so browsers don't see each other's changes.
    A visitor's demo is forgotten after IDLE_SECONDS without use; past MAX_DEMOS, the least recently used goes."""

    IDLE_SECONDS = 3600
    MAX_DEMOS = 200

    def __init__(self):
        self._lock = threading.Lock()
        self._demos: OrderedDict[str, tuple[Demo, float]] = OrderedDict()

    def _forget_idle(self) -> None:
        limit = time.monotonic() - self.IDLE_SECONDS
        while self._demos:
            visitor, (demo, used) = next(iter(self._demos.items()))
            if used >= limit and len(self._demos) <= self.MAX_DEMOS:
                break
            del self._demos[visitor]
            demo.close()

    def start(self, lang: str = "en") -> str:
        """A new visitor id with its own sample data."""
        visitor = secrets.token_urlsafe(16)
        demo = Demo()
        demo.sessionmaker(lang)
        with self._lock:
            self._demos[visitor] = (demo, time.monotonic())
            self._forget_idle()
        return visitor

    def get(self, visitor: str | None) -> Demo | None:
        with self._lock:
            self._forget_idle()
            entry = self._demos.get(visitor or "")
            if entry is None:
                return None
            self._demos[visitor] = (entry[0], time.monotonic())
            self._demos.move_to_end(visitor)
            return entry[0]

    def stop(self, visitor: str | None) -> None:
        with self._lock:
            entry = self._demos.pop(visitor or "", None)
        if entry is not None:
            entry[0].close()

    def close(self) -> None:
        with self._lock:
            demos, self._demos = list(self._demos.values()), OrderedDict()
        for demo, _ in demos:
            demo.close()
