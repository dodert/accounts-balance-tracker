"""What runs around every request: blocking cross-site writes, the login, and the security headers."""
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from .auth import COOKIE
from .deps import Context, is_https
from .users import USER_COOKIE

# Multi-user mode: reachable without logging in (with /invite/<token>)
MULTI_USER_PUBLIC = {"/recover"}
# Multi-user mode: these start the demo, also without an account
DEMO_ENTRY = {"/demo", "/api/demo"}
# Scripts only from the app itself (no inline ones); inline style attributes are used by the charts
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
# The interactive API docs (/docs, /redoc) are FastAPI's pages: an inline script and files from its CDN
DOCS_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https://fastapi.tiangolo.com; "
            "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
DOCS_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc"}
# The API docs and their schema (only with API_DOCS=true): multi-user mode shows them only to logged-in users,
# not to demo visitors
API_DOCS = DOCS_PATHS | {"/openapi.json"}


def foreign_request(request: Request) -> bool:
    """Sent by a page of another origin: another website, or another app on the same host (another
    port) or domain, which browsers call same-site and send the SameSite=Lax cookie to."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")  # none: typed or bookmarked by the user
    # Browsers without Sec-Fetch-Site: compare the Origin they send with the host asked for
    origin = request.headers.get("origin")
    return origin is not None and (origin == "null" or urlsplit(origin).netloc != request.headers.get("host"))


def login_required(request: Request) -> Response:
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "Login required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def install(app: FastAPI, ctx: Context) -> None:
    auth, users, multi = ctx.auth, ctx.users, ctx.multi

    def is_admin(request: Request) -> bool:
        return auth.valid_session(request.cookies.get(COOKIE))

    @app.middleware("http")
    async def reject_cross_site_writes(request: Request, call_next):
        # Another website open in the browser must not be able to post forms here (e.g. a CSV import)
        if request.method not in ("GET", "HEAD", "OPTIONS") and foreign_request(request):
            return Response("Cross-site request blocked", status_code=403)
        return await call_next(request)

    @app.middleware("http")
    async def require_login(request: Request, call_next):
        path = request.url.path
        if not multi:
            if not auth.enabled or auth.is_public(path) or is_admin(request):
                return await call_next(request)
            return login_required(request)
        # Multi-user: the admin pages need the admin; the rest of the app needs a user
        if auth.is_public(path) or path in MULTI_USER_PUBLIC or path.startswith("/invite/"):
            return await call_next(request)
        if path == "/admin" or path.startswith("/admin/"):
            return await call_next(request) if is_admin(request) else login_required(request)
        session = await run_in_threadpool(users.session, request.cookies.get(USER_COOKIE))
        if session is not None:
            request.state.user = session
            return await call_next(request)
        # The demo can be tried without an account: each visitor gets their own sample data
        if not path.startswith("/account") and path not in API_DOCS and (path in DEMO_ENTRY or ctx.in_demo(request)):
            return await call_next(request)
        if is_admin(request) and not path.startswith("/api/"):
            return RedirectResponse("/admin", status_code=303)
        return login_required(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        # Added last, so it runs first: also on the answers of the checks above (401, 403...)
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", DOCS_CSP if request.url.path in DOCS_PATHS else CSP)
        headers.setdefault("X-Frame-Options", "DENY")  # no other website can show the app in a frame
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "same-origin")
        if is_https(request):
            headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
