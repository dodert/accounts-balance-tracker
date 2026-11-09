"""Accounts."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import DUPLICATE_NAME, Context, account_out, commit, get_account_or_404, one_account_out
from ..history import latest_balances
from ..models import Account
from ..schemas import AccountIn, AccountOut, AccountUpdate


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings, get_db = ctx.settings, ctx.get_db

    # --- Accounts ---

    @router.get("/api/accounts", response_model=list[AccountOut])
    def list_accounts(archived: bool = True, db: Session = Depends(get_db)):
        stmt = select(Account).order_by(Account.name)
        if not archived:
            stmt = stmt.where(Account.archived.is_(False))
        latest = latest_balances(db)
        return [account_out(a, latest) for a in db.scalars(stmt)]

    @router.post("/api/accounts", response_model=AccountOut, status_code=201)
    def create_account(data: AccountIn, db: Session = Depends(get_db)):
        values = data.model_dump()
        values["currency"] = values["currency"] or settings.default_currency
        account = Account(**values)
        db.add(account)
        commit(db, DUPLICATE_NAME)
        db.refresh(account)
        return one_account_out(db, account)

    @router.get("/api/accounts/{account_id}", response_model=AccountOut)
    def get_account(account_id: int, db: Session = Depends(get_db)):
        return one_account_out(db, get_account_or_404(db, account_id))

    @router.patch("/api/accounts/{account_id}", response_model=AccountOut)
    def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
        account = get_account_or_404(db, account_id)
        for key, value in data.model_dump(exclude_unset=True).items():
            if value is None and key in ("name", "type", "currency", "archived"):
                continue
            setattr(account, key, value)
        commit(db, DUPLICATE_NAME)
        db.refresh(account)
        return one_account_out(db, account)

    @router.delete("/api/accounts/{account_id}", status_code=204)
    def delete_account(account_id: int, db: Session = Depends(get_db)):
        db.delete(get_account_or_404(db, account_id))
        db.commit()

    return router
