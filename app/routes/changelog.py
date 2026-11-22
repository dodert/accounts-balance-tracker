"""The changelog in the app (#205): the releases from CHANGELOG.md, and per user the last version whose changes they
saw and whether to show a new version's changes on their own. Both are kept in the user's own database (the only one,
the user's encrypted one or the demo's), like the guided tour (#156), so they follow the user, not the browser."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..changelog import releases
from ..deps import Context
from ..models import AppSetting

SEEN, AUTO = "changelog_seen", "changelog_auto"


class ChangelogState(BaseModel):
    seen: str | None = Field(None, pattern=r"^\d{1,6}\.\d{1,6}\.\d{1,6}$")  # None = no version seen yet
    auto: bool = True  # show a new version's changes on their own


class ChangelogUpdate(BaseModel):
    seen: str | None = Field(None, pattern=r"^\d{1,6}\.\d{1,6}\.\d{1,6}$")
    auto: bool | None = None


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    get_db = ctx.get_db

    def state(db: Session) -> dict:
        seen, auto = db.get(AppSetting, SEEN), db.get(AppSetting, AUTO)
        return {"seen": seen.value if seen else None, "auto": auto is None or auto.value != "0"}

    def store(db: Session, key: str, value: str) -> None:
        setting = db.get(AppSetting, key)
        if setting is None:
            db.add(AppSetting(key=key, value=value))
        else:
            setting.value = value

    @router.get("/api/changelog")
    def changelog():
        return {"version": ctx.version, "releases": list(releases())}

    @router.get("/api/changelog/state", response_model=ChangelogState)
    def changelog_state(db: Session = Depends(get_db)):
        return state(db)

    @router.put("/api/changelog/state", response_model=ChangelogState)
    def update_changelog_state(data: ChangelogUpdate, db: Session = Depends(get_db)):
        if data.seen is not None:
            store(db, SEEN, data.seen)
        if data.auto is not None:
            store(db, AUTO, "1" if data.auto else "0")
        db.commit()  # one commit for both: each one re-encrypts the whole file in multi-user
        return state(db)

    return router
