from datetime import date as Date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .models import AccountType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")]
Amount = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
Note = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
# Account notes: longer, free text
Notes = Annotated[str, StringConstraints(max_length=5000)]


def _blank_is_none(value):
    return None if isinstance(value, str) and not value.strip() else value


class AccountIn(BaseModel):
    name: Name
    type: AccountType = AccountType.bank
    currency: Currency | None = None  # None -> DEFAULT_CURRENCY
    institution: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    available_from: Date | None = None
    notes: Notes | None = None
    archived: bool = False

    _blanks = field_validator("institution", "notes", "currency", "available_from", mode="before")(_blank_is_none)


class AccountUpdate(BaseModel):
    name: Name | None = None
    type: AccountType | None = None
    currency: Currency | None = None
    institution: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    available_from: Date | None = None
    notes: Notes | None = None
    archived: bool | None = None

    _blanks = field_validator("institution", "notes", "available_from", mode="before")(_blank_is_none)


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: AccountType
    currency: str
    institution: str | None
    available_from: Date | None = None
    notes: str | None
    archived: bool
    balance: float | None = None
    balance_date: Date | None = None
    previous_balance: float | None = None
    previous_date: Date | None = None
    # The first balance recorded, to show the change since then
    first_balance: float | None = None
    first_date: Date | None = None


class BalanceIn(BaseModel):
    date: Date
    amount: Amount
    note: Note | None = None

    _blanks = field_validator("note", mode="before")(_blank_is_none)


class BalanceUpdate(BaseModel):
    date: Date | None = None
    amount: Amount | None = None
    note: Note | None = None

    _blanks = field_validator("note", mode="before")(_blank_is_none)


class MilestoneIn(BaseModel):
    date: Date
    amount: Amount
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class MilestoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    date: Date
    amount: float
    description: str


class BalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    date: Date
    amount: float
    note: str | None


class BulkItem(BaseModel):
    account_id: int
    amount: Amount


class BulkBalancesIn(BaseModel):
    date: Date
    items: list[BulkItem] = Field(max_length=1000)


class BulkBalancesOut(BaseModel):
    created: int
    updated: int


class CurrencyTotal(BaseModel):
    currency: str
    total: float
    accounts: int
    # Total on the previous date with a recorded balance, to show the change
    previous_total: float | None
    previous_date: Date | None
    # Total on the first date with a recorded balance
    first_total: float | None
    first_date: Date | None
    date: Date | None
    # Part of the total that can be used today (accounts whose available_from has come)
    available: float
    # Available part on the first date with a recorded balance
    first_available: float | None = None


class FxRateOut(BaseModel):
    base: str
    quote: str
    day: Date
    rate: float
    rate_date: Date
    fetched_at: datetime
    stale: bool


class CombinedTotal(BaseModel):
    """Every currency added up in the default currency, at today's rates."""
    currency: str
    total: float
    available: float
    rates: list[FxRateOut]
    # Currencies left out: no rate stored and the service could not be reached
    missing: list[str]


class Series(BaseModel):
    account_id: int
    name: str
    archived: bool
    # One value per entry of HistoryOut.dates: the last balance known on that day, None before the first
    values: list[float | None]
    # Only in the converted history: the account's own currency, and its first balance converted
    currency: str | None = None
    start: float | None = None


class HistoryOut(BaseModel):
    currency: str
    dates: list[Date]
    total: list[float]
    # Part of the total available on each date
    available: list[float]
    accounts: list[Series]
    # Only in the converted history (every currency in the default one, at each date's rate):
    first_total: float | None = None       # the total and the available part on the first date ever
    first_available: float | None = None
    converted: list[str] = []              # currencies converted
    missing: list[str] = []                # currencies left out: no rate stored and the service down
    stale: bool = False                    # some dates use the nearest stored rate, not their own


class CsvImportOut(BaseModel):
    accounts_created: int
    balances_created: int
    balances_updated: int
    errors: list[str]
