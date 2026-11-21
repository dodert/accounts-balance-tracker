"""The guided tour (#156): which version of it this user has already seen, kept in their own database (the only
one, the user's encrypted one or the demo's), so it is shown once per user whatever the browser."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import Context
from ..models import AppSetting

KEY = "tour_seen"


class TourSeen(BaseModel):
    seen: int = Field(ge=0, le=1000)  # the tour's version (app/static/js/tour.js), 0 = never


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db = ctx.get_db

    @router.get("/api/tour", response_model=TourSeen)
    def tour_seen(db: Session = Depends(get_db)):
        setting = db.get(AppSetting, KEY)
        return {"seen": int(setting.value) if setting and setting.value.isdigit() else 0}

    @router.put("/api/tour", response_model=TourSeen)
    def mark_tour_seen(data: TourSeen, db: Session = Depends(get_db)):
        setting = db.get(AppSetting, KEY)
        if setting is None:
            db.add(AppSetting(key=KEY, value=str(data.seen)))
        else:
            setting.value = str(data.seen)
        db.commit()
        return data

    return router
