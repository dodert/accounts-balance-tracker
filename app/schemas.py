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
    model_validator,
)

from .models import AccountType, PrepaymentMode

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Currency = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Za-z]{3}$")]
# Amounts in the JSON API, in and out, are integers in ten-thousandths (1234.5 -> 12345000; since 0.40, #120):
# exact in any client, also in JavaScript, whose numbers are binary floats but hold integers exactly up to 2**53.
# The server works with Decimal; ten-thousandths are only how amounts travel.
SCALE = 4  # decimals kept: the database's Numeric(16, 4)
UNIT = Decimal(1).scaleb(-SCALE)  # 0.0001
# The largest amount: SQLite stores amounts as binary floats, which keep 4 decimals exactly only below 2**39
MAX_AMOUNT = Decimal("499999999999.9999")
MAX_UNITS = int(MAX_AMOUNT.scaleb(SCALE))


def from_units(value) -> Decimal:
    if isinstance(value, Decimal):  # already an amount (built by the server itself)
        return value
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("amounts are integers in ten-thousandths: 1234.5 is 12345000")
    if abs(value) > MAX_UNITS:
        raise ValueError(f"amounts go up to {MAX_UNITS} ten-thousandths")
    return Decimal(value).scaleb(-SCALE)


def to_units(value: Decimal) -> int:
    """An amount in ten-thousandths, rounded half up (amounts converted at an exchange rate have more decimals)."""
    return int(Decimal(value).scaleb(SCALE).to_integral_value(ROUND_HALF_UP))


def amount_text(value: Decimal) -> str:
    """An amount as text for the CSV and JSON files: 2 decimals at least, up to 4 ("1234.50", "1.2345")."""
    text = f"{Decimal(value).quantize(UNIT, ROUND_HALF_UP):.{SCALE}f}"
    while text[-1] == "0" and len(text) - text.index(".") > 3:
        text = text[:-1]
    return text


_UNITS_SCHEMA = {"type": "integer", "description": "Amount in ten-thousandths: 1234.5 is 12345000"}
# An amount written as decimal text, as in the JSON backup file (json_io.py)
DecimalAmount = Annotated[Decimal, Field(max_digits=16, decimal_places=SCALE, le=MAX_AMOUNT, ge=-MAX_AMOUNT)]
# An amount sent to the API
Amount = Annotated[Decimal, BeforeValidator(from_units), WithJsonSchema(_UNITS_SCHEMA)]
# An amount the API sends
Units = Annotated[Decimal, PlainSerializer(to_units, return_type=int, when_used="json"), WithJsonSchema(_UNITS_SCHEMA)]
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
    balance: Units | None = None
    balance_date: Date | None = None
    previous_balance: Units | None = None
    previous_date: Date | None = None
    # The first balance recorded, to show the change since then
    first_balance: Units | None = None
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
    amount: Units
    description: str


class ContributionIn(BaseModel):
    date: Date
    amount: Amount  # negative: money taken out
    note: Note | None = None

    _blanks = field_validator("note", mode="before")(_blank_is_none)

    @field_validator("amount")
    @classmethod
    def not_zero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("a contribution can't be 0")
        return value


class ContributionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    date: Date
    amount: Units
    note: str | None


class BalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    account_id: int
    date: Date
    amount: Units
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
    total: Units
    accounts: int
    # Total on the previous date with a recorded balance, to show the change
    previous_total: Units | None
    previous_date: Date | None
    # Total on the first date with a recorded balance
    first_total: Units | None
    first_date: Date | None
    date: Date | None
    # Part of the total that can be used today (accounts whose available_from has come)
    available: Units
    # Available part on the first date with a recorded balance
    first_available: Units | None = None


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
    total: Units
    available: Units
    rates: list[FxRateOut]
    # Currencies left out: no rate stored and the service could not be reached
    missing: list[str]


class Series(BaseModel):
    account_id: int
    name: str
    archived: bool
    # One value per entry of HistoryOut.dates: the last balance known on that day, None before the first
    values: list[Units | None]
    # Only in the converted history: the account's own currency, and its first balance converted
    currency: str | None = None
    start: Units | None = None


class HistoryOut(BaseModel):
    currency: str
    dates: list[Date]
    total: list[Units]
    # Part of the total available on each date
    available: list[Units]
    accounts: list[Series]
    # Only in the converted history (every currency in the default one, at today's rate):
    first_total: Units | None = None       # the total and the available part on the first date ever
    first_available: Units | None = None
    converted: list[str] = []              # currencies converted
    missing: list[str] = []                # currencies left out: no rate stored and the service down
    stale: bool = False                    # today's rate could not be updated: the last stored one is used
    rates: dict[str, float] = {}           # today's rate of each converted currency (for the contributions, #148)


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
    contributions_created: int
    mortgages_created: int = 0
    prepayments_created: int = 0
    # What was already there (merge): not imported, listed (the first ones)
    duplicates_count: int
    duplicates: list[str]


# --- Mortgages (app/mortgage.py) ---

# An annual rate or a discount in %, with up to 4 decimals (2.95 is 2.95 %): sent and read as a JSON number
Rate = Annotated[Decimal, Field(ge=0, le=100, max_digits=7, decimal_places=4)]
RateOut = Annotated[Decimal, PlainSerializer(float, return_type=float, when_used="json")]


def _more_than_zero(value: Decimal) -> Decimal:
    if value <= 0:
        raise ValueError("must be more than 0")
    return value


def _not_negative(value: Decimal) -> Decimal:
    if value < 0:
        raise ValueError("can't be negative")
    return value


class MortgageIn(BaseModel):
    name: Name
    institution: Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)] | None = None
    currency: Currency | None = None  # None -> DEFAULT_CURRENCY
    principal: Amount
    rate: Rate  # before the discount
    discount: Rate = Decimal(0)
    term_months: int = Field(ge=1, le=600)
    first_payment: Date
    payment_day: int | None = Field(None, ge=1, le=31)  # None -> the day of the first payment
    notes: Notes | None = None

    _blanks = field_validator("institution", "notes", "currency", mode="before")(_blank_is_none)
    _positive = field_validator("principal")(_more_than_zero)

    @field_validator("discount")
    @classmethod
    def discount_below_rate(cls, value: Decimal, info) -> Decimal:
        rate = info.data.get("rate")
        if rate is not None and value > rate:
            raise ValueError("the discount can't be more than the rate")
        return value


class MortgageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    institution: str | None
    currency: str
    principal: Units
    rate: RateOut
    discount: RateOut
    term_months: int
    first_payment: Date
    payment_day: int
    notes: str | None


class PrepaymentIn(BaseModel):
    date: Date
    amount: Amount
    mode: PrepaymentMode
    fee: Amount = Decimal(0)
    note: Note | None = None

    _blanks = field_validator("note", mode="before")(_blank_is_none)
    _positive = field_validator("amount")(_more_than_zero)
    _fee = field_validator("fee")(_not_negative)


class PrepaymentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mortgage_id: int
    date: Date
    amount: Units
    mode: PrepaymentMode
    fee: Units
    note: str | None


class ScheduleRow(BaseModel):
    number: int
    date: Date
    payment: Units
    interest: Units
    principal: Units
    outstanding: Units  # after this installment
    paid: bool          # its date has come (the user's today)


class AppliedPrepayment(BaseModel):
    id: int | None
    date: Date
    amount: Units
    applied: Units      # what lowered the principal: less than the amount when that was more than what was left
    mode: PrepaymentMode
    fee: Units
    note: str | None
    outstanding: Units  # after it
    before_installment: int | None  # the next installment's number; None when nothing was left to repay


class ScheduleTotals(BaseModel):
    applied_rate: RateOut
    first_payment: Units
    current_payment: Units | None   # the next installment's
    next_date: Date | None
    outstanding: Units               # today
    principal_paid: Units            # by the installments paid so far
    interest_paid: Units
    prepaid: Units                   # prepayments made so far
    fees_paid: Units
    installments_paid: int
    installments_left: int
    end_date: Date | None
    total_interest: Units            # over the whole loan
    total_prepaid: Units
    total_fees: Units
    installments: int


class ScheduleOut(BaseModel):
    mortgage: MortgageOut
    rows: list[ScheduleRow]
    prepayments: list[AppliedPrepayment]
    totals: ScheduleTotals


class SimulatedPrepayment(BaseModel):
    date: Date
    amount: Amount
    mode: PrepaymentMode = PrepaymentMode.reduce_term
    fee: Amount = Decimal(0)

    _positive = field_validator("amount")(_more_than_zero)
    _fee = field_validator("fee")(_not_negative)


class SimulatedMonthly(BaseModel):
    """An extra amount every month, from `start` until the end."""
    start: Date
    amount: Amount
    mode: PrepaymentMode = PrepaymentMode.reduce_term
    fee: Amount = Decimal(0)  # each month

    _positive = field_validator("amount")(_more_than_zero)
    _fee = field_validator("fee")(_not_negative)


class SimulateIn(BaseModel):
    prepayments: list[SimulatedPrepayment] = Field([], max_length=100)
    monthly: SimulatedMonthly | None = None

    @model_validator(mode="after")
    def something_to_simulate(self):
        if not self.prepayments and self.monthly is None:
            raise ValueError("add a prepayment or a monthly extra payment to simulate")
        return self


class Comparison(BaseModel):
    interest_saved: Units
    fees: Units
    net_saved: Units
    months_saved: int
    end_date: Date | None
    payment_before: Units | None
    payment_after: Units | None


class Simulation(BaseModel):
    totals: ScheduleTotals
    compared: Comparison
    outstanding: list[Units]  # after each installment, by number (shorter when it ends sooner)


class SimulateOut(BaseModel):
    base: ScheduleTotals            # the mortgage as it is (real prepayments included)
    as_entered: Simulation          # with the simulated prepayments as entered
    reduce_payment: Simulation      # the same prepayments, all lowering the payment
    reduce_term: Simulation         # the same prepayments, all shortening the term
