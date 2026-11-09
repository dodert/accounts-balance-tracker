"""Balances, one by one or several on the same day."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Context, commit, get_account_or_404, get_balance_or_404
from ..models import Account, Balance
from ..schemas import BalanceIn, BalanceOut, BalanceUpdate, BulkBalancesIn, BulkBalancesOut


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db = ctx.get_db

    # --- Balances ---

    @router.get("/api/accounts/{account_id}/balances", response_model=list[BalanceOut])
    def list_balances(account_id: int, db: Session = Depends(get_db)):
        get_account_or_404(db, account_id)
        return db.scalars(select(Balance).where(Balance.account_id == account_id).order_by(Balance.date.desc())).all()

    @router.post("/api/accounts/{account_id}/balances", response_model=BalanceOut, status_code=201)
    def add_balance(account_id: int, data: BalanceIn, db: Session = Depends(get_db)):
        """Record the balance on a day; replaces the one already recorded on that day, if any."""
        get_account_or_404(db, account_id)
        balance = db.scalar(select(Balance).where(Balance.account_id == account_id, Balance.date == data.date))
        if balance:
            balance.amount, balance.note = data.amount, data.note
        else:
            balance = Balance(account_id=account_id, **data.model_dump())
            db.add(balance)
        db.commit()
        db.refresh(balance)
        return balance

    @router.patch("/api/balances/{balance_id}", response_model=BalanceOut)
    def update_balance(balance_id: int, data: BalanceUpdate, db: Session = Depends(get_db)):
        balance = get_balance_or_404(db, balance_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None and key in ("date", "amount"):
                continue
            setattr(balance, key, value)
        commit(db, "This account already has a balance on that date")
        db.refresh(balance)
        return balance

    @router.delete("/api/balances/{balance_id}", status_code=204)
    def delete_balance(balance_id: int, db: Session = Depends(get_db)):
        db.delete(get_balance_or_404(db, balance_id))
        db.commit()

    @router.post("/api/balances/bulk", response_model=BulkBalancesOut)
    def bulk_balances(data: BulkBalancesIn, db: Session = Depends(get_db)):
        """Record the balance of several accounts on the same day (the "Update balances" form)."""
        ids = [item.account_id for item in data.items]
        if len(set(ids)) != len(ids):
            raise HTTPException(422, "Each account can only appear once")
        known = set(db.scalars(select(Account.id).where(Account.id.in_(ids))))
        if unknown := set(ids) - known:
            raise HTTPException(422, f"Unknown account ids: {sorted(unknown)}")
        existing = {b.account_id: b for b in db.scalars(
            select(Balance).where(Balance.account_id.in_(ids), Balance.date == data.date))}
        created = updated = 0
        for item in data.items:
            if balance := existing.get(item.account_id):
                balance.amount = item.amount
                updated += 1
            else:
                db.add(Balance(account_id=item.account_id, date=data.date, amount=item.amount))
                created += 1
        db.commit()
        return BulkBalancesOut(created=created, updated=updated)

    return router
