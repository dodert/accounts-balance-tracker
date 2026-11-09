"""Health, version and languages."""
import json
from datetime import datetime

from fastapi import APIRouter, Request
from sqlalchemy import text

from ..deps import STATIC_DIR, Context


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings, auth, users, multi, version, SessionLocal, in_demo = (
        ctx.settings, ctx.auth, ctx.users, ctx.multi, ctx.version, ctx.SessionLocal, ctx.in_demo)

    # --- Info ---

    @router.get("/api/health")
    def health():
        return {"status": "ok", "version": version}

    @router.get("/api/about")
    def about(request: Request):
        if multi:
            database, schema = "sqlite", users.databases.head
        else:
            with SessionLocal() as db:
                schema = db.scalar(text("SELECT version_num FROM alembic_version"))
            database = "sqlite"
        session = getattr(request.state, "user", None)
        expires = None
        if session is not None:
            user = users.get_user(session.user_id)
            expires = user.expires_at if user else None
        return {
            "version": version,
            "commit": settings.git_sha or None,
            "ref": settings.git_ref or None,
            "build_date": settings.build_date or None,
            "database": database,
            "schema": schema,
            "auth": auth.enabled,
            "user": session.username if session else None,
            "expires": expires.isoformat() if expires else None,
            "days_left": max(0, (expires - datetime.now()).days) if expires else None,
            "demo": in_demo(request),
            "default_currency": settings.default_currency,
        }

    @router.get("/api/languages")
    def list_languages():
        """Interface languages: one JSON file per language in static/i18n, named by its code."""
        languages = []
        for path in sorted((STATIC_DIR / "i18n").glob("*.json")):
            name = json.loads(path.read_text(encoding="utf-8")).get("_name", path.stem)
            languages.append({"code": path.stem, "name": name})
        return languages

    return router
