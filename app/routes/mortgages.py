"""Mortgages: fixed-rate loans with their partial prepayments; the amortization schedule is computed (app/mortgage.py)
from them on every request, never stored. The simulator computes with prepayments that are not saved."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..deps import Context
from ..models import Mortgage, Prepayment, PrepaymentMode
from ..mortgage import Extra, Loan, Monthly, compare, schedule
from ..schemas import (
    MortgageIn,
    MortgageOut,
    PrepaymentIn,
    PrepaymentOut,
    ScheduleOut,
    SimulateIn,
    SimulateOut,
)


def get_mortgage_or_404(db: Session, mortgage_id: int) -> Mortgage:
    mortgage = db.get(Mortgage, mortgage_id)
    if not mortgage:
        raise HTTPException(404, "Mortgage not found")
    return mortgage


def get_prepayment_or_404(db: Session, prepayment_id: int) -> Prepayment:
    prepayment = db.get(Prepayment, prepayment_id)
    if not prepayment:
        raise HTTPException(404, "Prepayment not found")
    return prepayment


def loan_of(m: Mortgage) -> Loan:
    return Loan(principal=m.principal, rate=m.rate, discount=m.discount, term_months=m.term_months,
                first_payment=m.first_payment, payment_day=m.payment_day)


def extras_of(m: Mortgage) -> list[Extra]:
    return [Extra(date=p.date, amount=p.amount, mode=p.mode.value, fee=p.fee, id=p.id, note=p.note)
            for p in m.prepayments]


def schedule_out(m: Mortgage) -> dict:
    s = schedule(loan_of(m), extras_of(m), clock.today())
    return {
        "mortgage": MortgageOut.model_validate(m),
        "rows": [vars(r) for r in s.rows],
        "prepayments": [{"id": p.extra.id, "date": p.extra.date, "amount": p.extra.amount, "applied": p.applied,
                         "mode": p.extra.mode, "fee": p.extra.fee, "note": p.extra.note, "outstanding": p.outstanding,
                         "before_installment": p.before_installment} for p in s.prepayments],
        "totals": s.totals,
    }


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db, settings = ctx.get_db, ctx.settings

    def fill(m: Mortgage, data: MortgageIn) -> None:
        values = data.model_dump()
        values["currency"] = values["currency"] or settings.default_currency
        values["payment_day"] = values["payment_day"] or data.first_payment.day
        for key, value in values.items():
            setattr(m, key, value)

    @router.get("/api/mortgages", response_model=list[MortgageOut])
    def list_mortgages(db: Session = Depends(get_db)):
        return db.scalars(select(Mortgage).order_by(Mortgage.name, Mortgage.id)).all()

    @router.post("/api/mortgages", response_model=MortgageOut, status_code=201)
    def add_mortgage(data: MortgageIn, db: Session = Depends(get_db)):
        mortgage = Mortgage()
        fill(mortgage, data)
        db.add(mortgage)
        db.commit()
        db.refresh(mortgage)
        return mortgage

    @router.get("/api/mortgages/{mortgage_id}", response_model=MortgageOut)
    def get_mortgage(mortgage_id: int, db: Session = Depends(get_db)):
        return get_mortgage_or_404(db, mortgage_id)

    @router.put("/api/mortgages/{mortgage_id}", response_model=MortgageOut)
    def update_mortgage(mortgage_id: int, data: MortgageIn, db: Session = Depends(get_db)):
        mortgage = get_mortgage_or_404(db, mortgage_id)
        fill(mortgage, data)
        db.commit()
        db.refresh(mortgage)
        return mortgage

    @router.delete("/api/mortgages/{mortgage_id}", status_code=204)
    def delete_mortgage(mortgage_id: int, db: Session = Depends(get_db)):
        db.delete(get_mortgage_or_404(db, mortgage_id))
        db.commit()

    @router.get("/api/mortgages/{mortgage_id}/schedule", response_model=ScheduleOut)
    def get_schedule(mortgage_id: int, db: Session = Depends(get_db)):
        """The amortization schedule, computed now: installments whose date has come (the user's today) are paid."""
        return schedule_out(get_mortgage_or_404(db, mortgage_id))

    @router.post("/api/mortgages/{mortgage_id}/simulate", response_model=SimulateOut)
    def simulate(mortgage_id: int, data: SimulateIn, db: Session = Depends(get_db)):
        """The schedule with some more prepayments and/or an extra payment every month, compared with the real one.
        Nothing is saved."""
        mortgage = get_mortgage_or_404(db, mortgage_id)
        loan, real, today = loan_of(mortgage), extras_of(mortgage), clock.today()
        base = schedule(loan, real, today)

        def run(mode: PrepaymentMode | None) -> dict:
            extras = [Extra(date=p.date, amount=p.amount, mode=(mode or p.mode).value, fee=p.fee)
                      for p in data.prepayments]
            m = data.monthly
            monthly = m and Monthly(start=m.start, amount=m.amount, mode=(mode or m.mode).value, fee=m.fee)
            s = schedule(loan, real + extras, today, monthly)
            return {"totals": s.totals, "compared": compare(base, s, extras, monthly),
                    "outstanding": [r.outstanding for r in s.rows]}

        return {"base": base.totals, "as_entered": run(None),
                "reduce_payment": run(PrepaymentMode.reduce_payment), "reduce_term": run(PrepaymentMode.reduce_term)}

    # --- Prepayments ---

    @router.get("/api/mortgages/{mortgage_id}/prepayments", response_model=list[PrepaymentOut])
    def list_prepayments(mortgage_id: int, db: Session = Depends(get_db)):
        return get_mortgage_or_404(db, mortgage_id).prepayments

    @router.post("/api/mortgages/{mortgage_id}/prepayments", response_model=PrepaymentOut, status_code=201)
    def add_prepayment(mortgage_id: int, data: PrepaymentIn, db: Session = Depends(get_db)):
        get_mortgage_or_404(db, mortgage_id)
        prepayment = Prepayment(mortgage_id=mortgage_id, **data.model_dump())
        db.add(prepayment)
        db.commit()
        db.refresh(prepayment)
        return prepayment

    @router.put("/api/prepayments/{prepayment_id}", response_model=PrepaymentOut)
    def update_prepayment(prepayment_id: int, data: PrepaymentIn, db: Session = Depends(get_db)):
        prepayment = get_prepayment_or_404(db, prepayment_id)
        for key, value in data.model_dump().items():
            setattr(prepayment, key, value)
        db.commit()
        db.refresh(prepayment)
        return prepayment

    @router.delete("/api/prepayments/{prepayment_id}", status_code=204)
    def delete_prepayment(prepayment_id: int, db: Session = Depends(get_db)):
        db.delete(get_prepayment_or_404(db, prepayment_id))
        db.commit()

    return router
