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


class Balance(Base):
    """The balance of an account on a day. One per account and day."""
    __tablename__ = "balances"
    __table_args__ = (UniqueConstraint("account_id", "date", name="uq_balance_account_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="balances")


class Milestone(Base):
    """A notable expense of an account (e.g. a car, a trip), drawn as a vertical line on the charts."""
    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="milestones")


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
