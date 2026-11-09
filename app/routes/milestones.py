"""Milestones: notable expenses, drawn as vertical lines on the charts."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import Context, get_account_or_404, get_milestone_or_404
from ..models import Milestone
from ..schemas import MilestoneIn, MilestoneOut


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db = ctx.get_db

    # --- Milestones: notable expenses, drawn as vertical lines on the charts ---

    @router.get("/api/milestones", response_model=list[MilestoneOut])
    def list_milestones(account_id: int | None = None, db: Session = Depends(get_db)):
        stmt = select(Milestone).order_by(Milestone.date, Milestone.id)
        if account_id is not None:
            stmt = stmt.where(Milestone.account_id == account_id)
        return db.scalars(stmt).all()

    @router.post("/api/accounts/{account_id}/milestones", response_model=MilestoneOut, status_code=201)
    def add_milestone(account_id: int, data: MilestoneIn, db: Session = Depends(get_db)):
        get_account_or_404(db, account_id)
        milestone = Milestone(account_id=account_id, **data.model_dump())
        db.add(milestone)
        db.commit()
        db.refresh(milestone)
        return milestone

    @router.put("/api/milestones/{milestone_id}", response_model=MilestoneOut)
    def update_milestone(milestone_id: int, data: MilestoneIn, db: Session = Depends(get_db)):
        milestone = get_milestone_or_404(db, milestone_id)
        for key, value in data.model_dump().items():
            setattr(milestone, key, value)
        db.commit()
        db.refresh(milestone)
        return milestone

    @router.delete("/api/milestones/{milestone_id}", status_code=204)
    def delete_milestone(milestone_id: int, db: Session = Depends(get_db)):
        db.delete(get_milestone_or_404(db, milestone_id))
        db.commit()

    return router
