"""Demo mode: made-up sample data in a database apart from the real one."""
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse, Response

from ..demo import COOKIE as DEMO_COOKIE
from ..deps import Context


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    multi, visitor_demos, in_demo, visitor_demo, demo_lang, set_demo = (
        ctx.multi, ctx.visitor_demos, ctx.in_demo, ctx.visitor_demo, ctx.demo_lang, ctx.set_demo)

    # --- Demo mode ---

    @router.post("/api/demo", status_code=204)
    def start_demo(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """Demo mode on for this browser: made-up sample data, in a database apart from the real one."""
        return set_demo(Response(status_code=204), request, True, lang)

    @router.delete("/api/demo", status_code=204)
    def stop_demo(request: Request):
        """Back to the real data."""
        if multi:
            visitor_demos.stop(request.cookies.get(DEMO_COOKIE))
        return set_demo(Response(status_code=204), request, False)

    @router.post("/api/demo/reset", status_code=204)
    def reset_demo(request: Request, lang: str = Query(None, pattern=r"^[a-z]{2}$")):
        """Build the sample data again (undoes the changes made in demo mode)."""
        if not in_demo(request):
            raise HTTPException(409, "Not in demo mode")
        lang = lang or demo_lang(request)
        visitor_demo(request).reset(lang)
        return set_demo(Response(status_code=204), request, True, lang)

    @router.get("/demo", include_in_schema=False)
    def demo_link(request: Request, lang: str = Query("en", pattern=r"^[a-z]{2}$")):
        """A link that opens the app in demo mode (e.g. to show it to someone)."""
        return set_demo(RedirectResponse("/", status_code=303), request, True, lang)

    return router
