"""Contributions: money put into (or taken out of) an account, drawn on its chart next to its balance."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Context, get_account_or_404, get_contribution_or_404
from ..models import Contribution
from ..schemas import ContributionIn, ContributionOut


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db = ctx.get_db

    @router.get("/api/contributions", response_model=list[ContributionOut])
    def list_contributions(account_id: int | None = None, db: Session = Depends(get_db)):
        stmt = select(Contribution).order_by(Contribution.date, Contribution.id)
        if account_id is not None:
            stmt = stmt.where(Contribution.account_id == account_id)
        return db.scalars(stmt).all()

    @router.post("/api/accounts/{account_id}/contributions", response_model=ContributionOut, status_code=201)
    def add_contribution(account_id: int, data: ContributionIn, db: Session = Depends(get_db)):
        get_account_or_404(db, account_id)
        contribution = Contribution(account_id=account_id, **data.model_dump())
        db.add(contribution)
        db.commit()
        db.refresh(contribution)
        return contribution

    @router.put("/api/contributions/{contribution_id}", response_model=ContributionOut)
    def update_contribution(contribution_id: int, data: ContributionIn, db: Session = Depends(get_db)):
        contribution = get_contribution_or_404(db, contribution_id)
        for key, value in data.model_dump().items():
            setattr(contribution, key, value)
        db.commit()
        db.refresh(contribution)
        return contribution

    @router.delete("/api/contributions/{contribution_id}", status_code=204)
    def delete_contribution(contribution_id: int, db: Session = Depends(get_db)):
        db.delete(get_contribution_or_404(db, contribution_id))
        db.commit()

    return router
