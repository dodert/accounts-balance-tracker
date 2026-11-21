import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class AccountType(str, enum.Enum):
    bank = "bank"
    savings = "savings"
    investment = "investment"
    pension = "pension"
    crypto = "crypto"
    cash = "cash"
    loan = "loan"
    other = "other"


# The types whose accounts record contributions (money put in): their balance is what the money is worth
INVESTMENT_TYPES = (AccountType.investment, AccountType.pension, AccountType.crypto)


class Account(Base):
    __tablename__ = "accounts"
    # The name is unique per institution (no institution counts as one more): same index as migration 0002
    __table_args__ = (Index("uq_account_name_institution", "name", text("coalesce(institution, '')"), unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, native_enum=False, length=10, values_callable=lambda e: [m.value for m in e]),
        default=AccountType.bank,
    )
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    institution: Mapped[str | None] = mapped_column(String(100))
    # The balance cannot be used before this day (e.g. unvested stock options); None = available
    available_from: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    balances: Mapped[list["Balance"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True, order_by="Balance.date",
    )
    milestones: Mapped[list["Milestone"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True, order_by="Milestone.date",
    )
    contributions: Mapped[list["Contribution"]] = relationship(
        back_populates="account", cascade="all, delete-orphan", passive_deletes=True,
        order_by="[Contribution.date, Contribution.id]",
    )


class Balance(Base):
    """The balance of an account on a day. One per account and day."""
    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_balance_account_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="balances")


class Milestone(Base):
    """A notable expense of an account (e.g. a car, a trip), drawn as a vertical line on the charts."""
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    description: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="milestones")


class Contribution(Base):
    """Money put into an account (e.g. an investment fund) on a day; negative when it was taken out. The
    charts draw what was contributed so far next to the balance (what it is worth), and the gain between them."""
    __tablename__ = "contributions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="contributions")


class FxRate(Base):
    """An exchange rate: 1 `base` = `rate` `quote`, for a day. See app/fx.py."""
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("base", "quote", "day", name="uq_fx_rate_pair_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    day: Mapped[date] = mapped_column(Date)  # the day it was asked for
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    rate_date: Mapped[date] = mapped_column(Date)  # the day the rate is from
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class PrepaymentMode(str, enum.Enum):
    """What a partial prepayment lowers: the monthly payment (same remaining term) or the term (same payment)."""
    reduce_payment = "reduce_payment"
    reduce_term = "reduce_term"


class Mortgage(Base):
    """A fixed-rate mortgage (French system: the same payment every month). Its amortization schedule is not stored:
    app/mortgage.py computes it from these fields and the prepayments. Not linked to any account."""
    __tablename__ = "mortgages"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    institution: Mapped[str | None] = mapped_column(String(100))
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    principal: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    # Annual nominal rate (TIN) in %, before the discount; the applied rate is rate - discount
    rate: Mapped[Decimal] = mapped_column(Numeric(7, 4))
    discount: Mapped[Decimal] = mapped_column(Numeric(7, 4), default=Decimal(0))  # percentage points
    term_months: Mapped[int] = mapped_column(Integer)
    first_payment: Mapped[date] = mapped_column(Date)  # the date of the first installment
    payment_day: Mapped[int] = mapped_column(Integer)  # day of the month of the next ones (the last day if shorter)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    prepayments: Mapped[list["Prepayment"]] = relationship(
        back_populates="mortgage", cascade="all, delete-orphan", passive_deletes=True,
        order_by="[Prepayment.date, Prepayment.id]",
    )


class Prepayment(Base):
    """A partial early repayment of a mortgage. The fee is paid apart: it does not lower the principal."""
    __tablename__ = "mortgage_prepayments"

    id: Mapped[int] = mapped_column(primary_key=True)
    mortgage_id: Mapped[int] = mapped_column(ForeignKey("mortgages.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 4))
    mode: Mapped[PrepaymentMode] = mapped_column(
        Enum(PrepaymentMode, native_enum=False, length=20, values_callable=lambda e: [m.value for m in e]),
    )
    fee: Mapped[Decimal] = mapped_column(Numeric(16, 4), default=Decimal(0))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mortgage: Mapped[Mortgage] = relationship(back_populates="prepayments")
