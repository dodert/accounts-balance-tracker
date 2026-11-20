"""The amortization schedule of a fixed-rate mortgage, French system (the same payment every month), computed from
its fields and its partial prepayments. Pure functions: nothing is stored and nothing leaves the app.

- Monthly rate i = applied annual rate / 12 (applied rate = rate - discount).
- Payment P = C·i / (1 - (1+i)^-n) over the remaining term n (C / n at 0 %), rounded to cents.
- Each installment: interest = outstanding · i (rounded to cents), principal = P - interest. The last one pays
  whatever is left, so the rounding residue never carries over.
- Installment k is due on the payment day of the (k-1)th month after the first installment (the month's last day
  when it is shorter). It is paid once its date has come (the user's today): there are no edits per installment.
- A partial prepayment counts from the next installment due after its date (one on an installment's day comes
  after that installment); there is no daily interest for the days in between. It lowers the outstanding principal
  (never below 0), then:
  - reduce_payment: same remaining term, a new, lower payment;
  - reduce_term: same payment, fewer installments left.
  Its fee is paid apart: it does not lower the principal.
"""
import math
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, localcontext

CENT = Decimal("0.01")
ZERO = Decimal(0)
REDUCE_PAYMENT, REDUCE_TERM = "reduce_payment", "reduce_term"


@dataclass(frozen=True)
class Loan:
    principal: Decimal
    rate: Decimal          # annual, in %, before the discount
    discount: Decimal      # percentage points
    term_months: int
    first_payment: date
    payment_day: int

    @property
    def applied_rate(self) -> Decimal:
        return self.rate - self.discount


@dataclass(frozen=True)
class Extra:
    """A partial prepayment, real or simulated."""
    date: date
    amount: Decimal
    mode: str
    fee: Decimal = ZERO
    id: int | None = None
    note: str | None = None
    monthly: bool = False  # one of the payments of a Monthly extra


@dataclass(frozen=True)
class Monthly:
    """An extra amount paid every month, from `start` until the loan ends (simulated): right after each installment
    due on or after `start`, as a prepayment of `amount` (and its `fee`) in `mode`."""
    start: date
    amount: Decimal
    mode: str
    fee: Decimal = ZERO


@dataclass
class Row:
    number: int
    date: date
    payment: Decimal
    interest: Decimal
    principal: Decimal
    outstanding: Decimal  # after this installment
    paid: bool


@dataclass
class Applied:
    """A prepayment as it was applied: `applied` is what lowered the principal (less than the amount when the amount
    was more than what was left; 0 when it came after the end)."""
    extra: Extra
    applied: Decimal
    outstanding: Decimal  # after it
    before_installment: int | None  # the number of the next installment, None after the last one


@dataclass
class Schedule:
    loan: Loan
    rows: list[Row]
    prepayments: list[Applied]
    first_payment_amount: Decimal
    today: date
    totals: dict = field(default_factory=dict)


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def installment_date(loan: Loan, number: int) -> date:
    """The due date of installment `number` (1 = the first)."""
    if number == 1:
        return loan.first_payment
    months = loan.first_payment.year * 12 + loan.first_payment.month - 1 + number - 1
    year, month = divmod(months, 12)
    month += 1
    next_month = date(year + (month == 12), month % 12 + 1, 1)
    last_day = (next_month - date(year, month, 1)).days
    return date(year, month, min(loan.payment_day, last_day))


def monthly_rate(loan: Loan) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = 34
        return loan.applied_rate / Decimal(1200)


def annuity(outstanding: Decimal, i: Decimal, months: int) -> Decimal:
    """The monthly payment that repays `outstanding` in `months` installments at the monthly rate `i`."""
    if months <= 0:
        return _cents(outstanding)
    if i == 0:
        return _cents(outstanding / months)
    with localcontext() as ctx:
        ctx.prec = 34
        return _cents(outstanding * i / (1 - (1 + i) ** -months))


def months_to_repay(outstanding: Decimal, i: Decimal, payment: Decimal) -> int:
    """How many installments of `payment` repay `outstanding` (the last one smaller)."""
    if outstanding <= 0:
        return 0
    if i == 0:
        return math.ceil(outstanding / payment)
    interest = outstanding * i
    if payment <= interest:  # can't happen with a payment computed for this loan, but never loop forever
        raise ValueError("the payment does not cover the interest")
    n = -math.log(1 - float(interest / payment)) / math.log(1 + float(i))
    # A float error must not add an installment of a few cents: round down when it is that close
    return max(1, math.ceil(n - 1e-9))


def schedule(loan: Loan, extras: list[Extra], today: date, monthly: Monthly | None = None) -> Schedule:
    i = monthly_rate(loan)
    outstanding = Decimal(loan.principal)
    end = loan.term_months  # the number of the last installment
    payment = annuity(outstanding, i, end)
    first_payment = payment
    pending = sorted(extras, key=lambda e: (e.date, e.id or 0))
    applied: list[Applied] = []
    rows: list[Row] = []
    number = 1

    def prepay(extra: Extra, next_number: int) -> None:
        """Lower the outstanding principal by the prepayment, before installment `next_number`."""
        nonlocal outstanding, payment, end
        amount = min(extra.amount, outstanding)
        outstanding -= amount
        if outstanding > 0 and amount > 0:
            if extra.mode == REDUCE_PAYMENT:
                payment = annuity(outstanding, i, end - next_number + 1)
            else:
                end = next_number - 1 + months_to_repay(outstanding, i, payment)
        applied.append(Applied(extra, amount, outstanding, next_number if outstanding > 0 else None))

    while outstanding > 0 and number <= end:
        due = installment_date(loan, number)
        # Prepayments made before this installment (on the day of the previous one or later)
        while pending and pending[0].date < due:
            prepay(pending.pop(0), number)
        if outstanding <= 0:
            break
        interest = _cents(outstanding * i)
        principal = payment - interest
        if number == end or principal >= outstanding:
            principal = outstanding
        outstanding -= principal
        rows.append(Row(number, due, interest + principal, interest, principal, outstanding, due <= today))
        number += 1
        if monthly and due >= monthly.start and outstanding > 0:
            prepay(Extra(due, monthly.amount, monthly.mode, monthly.fee, monthly=True), number)
    for extra in pending:  # after the last installment: nothing left to repay
        applied.append(Applied(extra, ZERO, ZERO, None))
    result = Schedule(loan, rows, applied, first_payment, today)
    result.totals = totals(result)
    return result


def totals(s: Schedule) -> dict:
    today = s.today
    paid = [r for r in s.rows if r.paid]
    upcoming = [r for r in s.rows if not r.paid]
    past = [p for p in s.prepayments if p.extra.date <= today]
    prepaid = sum((p.applied for p in past), ZERO)
    principal_paid = sum((r.principal for r in paid), ZERO)
    total_interest = sum((r.interest for r in s.rows), ZERO)
    last = s.rows[-1].date if s.rows else None
    return {
        "applied_rate": s.loan.applied_rate,
        "first_payment": s.first_payment_amount,
        # The next installment (the last one when everything is paid)
        "current_payment": upcoming[0].payment if upcoming else None,
        "next_date": upcoming[0].date if upcoming else None,
        "outstanding": s.loan.principal - principal_paid - prepaid,
        "principal_paid": principal_paid,
        "interest_paid": sum((r.interest for r in paid), ZERO),
        "prepaid": prepaid,
        "fees_paid": sum((p.extra.fee for p in past), ZERO),
        "installments_paid": len(paid),
        "installments_left": len(upcoming),
        "end_date": last,
        # The whole life of the loan (past and future)
        "total_interest": total_interest,
        "total_prepaid": sum((p.applied for p in s.prepayments), ZERO),
        "total_fees": sum((p.extra.fee for p in s.prepayments), ZERO),
        "installments": len(s.rows),
    }


def months_between(a: date | None, b: date | None) -> int:
    if a is None or b is None:
        return 0
    return (b.year - a.year) * 12 + b.month - a.month


def compare(base: Schedule, simulated: Schedule, extras: list[Extra], monthly: Monthly | None = None) -> dict:
    """What the simulated prepayments `extras` (and the `monthly` extra payment) change: the interest saved (also
    net of their fees), the payment after the last of them (after the first monthly one) and the end date."""
    saved = base.totals["total_interest"] - simulated.totals["total_interest"]
    # A monthly payment's fee counts only for the months it was paid
    fees = sum((e.fee for e in extras), ZERO) + sum((p.extra.fee for p in simulated.prepayments
                                                     if p.extra.monthly and p.applied > 0), ZERO)
    # With a monthly payment: the date of its first one (after the first installment on or after its start)
    first_monthly = next((p.extra.date for p in simulated.prepayments if p.extra.monthly), None)
    dates = [e.date for e in extras] + ([first_monthly] if first_monthly else [])
    last = max(dates, default=None)
    after = next((r for r in simulated.rows if last is None or r.date > last), None)
    before = next((r for r in base.rows if last is None or r.date > last), None)
    return {
        "interest_saved": saved,
        "fees": fees,
        "net_saved": saved - fees,
        "months_saved": months_between(simulated.totals["end_date"], base.totals["end_date"]),
        "end_date": simulated.totals["end_date"],
        # The installment after the last simulated prepayment, with and without them (None: nothing left to pay)
        "payment_before": before.payment if before else None,
        "payment_after": after.payment if after else None,
    }
