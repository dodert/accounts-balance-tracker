"""Export and import: CSV (balances) and JSON (everything)."""
import asyncio
import logging
from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from ..csv_io import export_csv, import_csv
from ..deps import Context
from ..json_io import export_json, import_json, parse_backup
from ..migrate import backup_sqlite_before_import
from ..schemas import CsvImportOut, JsonImportOut

MAX_CSV_BYTES = 20 * 1024 * 1024
MAX_DEMO_CSV_BYTES = 1024 * 1024  # anyone can try the demo: smaller files, so one import can't take long
# Imports running at once (each takes a thread while it parses and stores the file)
IMPORTS_AT_ONCE = 2


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings, users, multi, version, engine, get_db, in_demo = (
        ctx.settings, ctx.users, ctx.multi, ctx.version, ctx.engine, ctx.get_db, ctx.in_demo)

    # --- CSV ---

    @router.get("/api/export/csv")
    def export(db: Session = Depends(get_db)):
        filename = f"balances-{date.today().isoformat()}.csv"
        return Response(export_csv(db), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    import_slots = asyncio.Semaphore(IMPORTS_AT_ONCE)

    @router.post("/api/import/csv", response_model=CsvImportOut)
    async def import_file(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
        limit = MAX_DEMO_CSV_BYTES if in_demo(request) else MAX_CSV_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File too large (most {limit // 1024 // 1024} MB)")
        try:
            # In a worker thread: a big file takes a while, and the app must keep answering meanwhile
            async with import_slots:
                return await run_in_threadpool(import_csv, db, data, settings.default_currency)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    # --- JSON: everything (accounts with all their fields, balances, milestones) ---

    @router.get("/api/export/json")
    def export_all(db: Session = Depends(get_db)):
        filename = f"balances-{date.today().isoformat()}.json"
        return Response(export_json(db, version), media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    def backup_before_replace(request: Request) -> None:
        """A copy of the data before an import replaces it. The demo's data is temporary: no copy."""
        if in_demo(request):
            return
        if multi:
            saved = users.databases.backup_before_import(request.state.user.user_id)
        else:
            saved = backup_sqlite_before_import(engine)
            if saved is None:  # an in-memory database (tests): nothing on disk to copy
                return
        logging.getLogger(__name__).warning("Backup made before replacing all the data with an import: %s", saved.name)

    @router.post("/api/import/json", response_model=JsonImportOut)
    async def import_all(request: Request, file: UploadFile = File(...), mode: str = Form(...),
                         db: Session = Depends(get_db)):
        if mode not in ("merge", "replace"):
            raise HTTPException(422, "mode must be merge or replace")
        limit = MAX_DEMO_CSV_BYTES if in_demo(request) else MAX_CSV_BYTES
        data = await file.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"File too large (most {limit // 1024 // 1024} MB)")

        def run():
            backup = parse_backup(data)  # the whole file is checked before anything changes
            if mode == "replace":
                backup_before_replace(request)
            return import_json(db, backup, replace=mode == "replace")
        try:
            async with import_slots:
                return await run_in_threadpool(run)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    return router
