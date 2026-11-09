from datetime import date as Date
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    field_validator,
)

from .models import AccountType

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")]
# Amounts in the JSON API, in and out, are integer cents (1234.50 -> 123450): exact in any client, also in
# JavaScript, whose numbers are binary floats but hold integers exactly up to 2**53. The server works with
# Decimal; cents are only how amounts travel.
MAX_CENTS = 10**14 - 1  # the database's Numeric(14, 2)


def from_cents(value) -> Decimal:
    if isinstance(value, Decimal):  # already an amount (built by the server itself)
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("amounts are integer cents: 1234.50 is 123450")
    if abs(value) > MAX_CENTS:
        raise ValueError(f"amounts go up to {MAX_CENTS} cents")
    return Decimal(value).scaleb(-2)


def to_cents(value: Decimal) -> int:
    """An amount in cents, rounded half up (amounts converted at an exchange rate have more decimals)."""
    return int((Decimal(value) * 100).to_integral_value(ROUND_HALF_UP))


_CENTS_SCHEMA = {"type": "integer", "description": "Amount in cents (minor units): 1234.50 is 123450"}
# An amount written as decimal text, as in the JSON backup file (json_io.py)
DecimalAmount = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]
# An amount sent to the API
Amount = Annotated[Decimal, BeforeValidator(from_cents), WithJsonSchema(_CENTS_SCHEMA)]
# An amount the API sends
Cents = Annotated[Decimal, PlainSerializer(to_cents, return_type=int, when_used="json"), WithJsonSchema(_CENTS_SCHEMA)]
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
    balance: Cents | None = None
    balance_date: Date | None = None
    previous_balance: Cents | None = None
    previous_date: Date | None = None
    # The first balance recorded, to show the change since then
    first_balance: Cents | None = None
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
    amount: Cents
    description: str


class BalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    date: Date
    amount: Cents
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
    total: Cents
    accounts: int
    # Total on the previous date with a recorded balance, to show the change
    previous_total: Cents | None
    previous_date: Date | None
    # Total on the first date with a recorded balance
    first_total: Cents | None
    first_date: Date | None
    date: Date | None
    # Part of the total that can be used today (accounts whose available_from has come)
    available: Cents
    # Available part on the first date with a recorded balance
    first_available: Cents | None = None


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
    total: Cents
    available: Cents
    rates: list[FxRateOut]
    # Currencies left out: no rate stored and the service could not be reached
    missing: list[str]


class Series(BaseModel):
    account_id: int
    name: str
    archived: bool
    # One value per entry of HistoryOut.dates: the last balance known on that day, None before the first
    values: list[Cents | None]
    # Only in the converted history: the account's own currency, and its first balance converted
    currency: str | None = None
    start: Cents | None = None


class HistoryOut(BaseModel):
    currency: str
    dates: list[Date]
    total: list[Cents]
    # Part of the total available on each date
    available: list[Cents]
    accounts: list[Series]
    # Only in the converted history (every currency in the default one, at today's rate):
    first_total: Cents | None = None       # the total and the available part on the first date ever
    first_available: Cents | None = None
    converted: list[str] = []              # currencies converted
    missing: list[str] = []                # currencies left out: no rate stored and the service down
    stale: bool = False                    # today's rate could not be updated: the last stored one is used


class CsvImportOut(BaseModel):
    accounts_created: int
    balances_created: int
    # Balances already recorded for that account and date: not imported (never overwritten), listed (the first ones)
    duplicates_count: int
    duplicates: list[str]
    errors: list[str]


class JsonImportOut(BaseModel):
    accounts_created: int
    balances_created: int
    milestones_created: int
    # What was already there (merge): not imported, listed (the first ones)
    duplicates_count: int
    duplicates: list[str]
