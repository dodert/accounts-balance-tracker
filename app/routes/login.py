"""Login (password and one-time code), logout and, in multi-user mode, sign-up, recovery and password change."""
import asyncio
import html
import math

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import totp as one_time
from ..auth import COOKIE, PENDING_COOKIE, PENDING_SECONDS, SESSION_SECONDS
from ..demo import COOKIE as DEMO_COOKIE
from ..deps import Context, client_ip, is_https, limit_key
from ..ratelimit import TooManyTries, TryLimit
from ..users import PENDING_SECONDS as USER_PENDING_SECONDS
from ..users import SESSION_SECONDS as USER_SESSION_SECONDS
from ..users import USER_COOKIE, USER_PENDING_COOKIE, SignupError

WRONG_PASSWORD_DELAY = 1  # seconds
# Wrong tries allowed per client IP in LOGIN_WINDOW seconds; then that IP waits (others don't)
WRONG_PASSWORDS_PER_IP = 10
WRONG_CODES_PER_IP = 10
# Wrong one-time codes from all IPs together (only reachable with the right password), so that many
# IPs can't guess the 6 digits between them
WRONG_CODES_TOTAL = 30
LOGIN_WINDOW = 15 * 60
# Wrong passwords for one user name from all IPs together, so many addresses (e.g. an IPv6 /64) can't
# guess one password between them. Recovery keys are not limited this way: they are too long to guess
WRONG_PASSWORDS_PER_USER = 30
# Password checks (Argon2id: 64 MB and ~0.1 s each) running at once; the rest wait without taking a thread
ARGON2_AT_ONCE = 4


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    auth, users, totp, countries, multi, visitor_demos, set_demo, pages = (
        ctx.auth, ctx.users, ctx.totp, ctx.countries, ctx.multi, ctx.visitor_demos, ctx.set_demo, ctx.pages)
    fill, message, english = pages.fill, pages.message, pages.english
    login_html, totp_html, message_html = pages.login, pages.totp, pages.message_page
    register_html, recovery_html, recover_html, account_html = pages.register, pages.recovery, pages.recover, pages.account

    password_tries = TryLimit(WRONG_PASSWORDS_PER_IP, LOGIN_WINDOW)
    user_password_tries = TryLimit(WRONG_PASSWORDS_PER_USER, LOGIN_WINDOW)
    argon2_slots = asyncio.Semaphore(ARGON2_AT_ONCE)

    async def with_password_check(function, *args):
        """Run a function that derives a key from a password (Argon2id), a few at a time."""
        async with argon2_slots:
            return await run_in_threadpool(function, *args)
    code_tries = TryLimit(WRONG_CODES_PER_IP, LOGIN_WINDOW, allowed_total=WRONG_CODES_TOTAL)

    def logged_in(request: Request) -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, auth.new_session(), max_age=SESSION_SECONDS, httponly=True, samesite="lax",
                            secure=is_https(request))
        response.delete_cookie(PENDING_COOKIE)
        return response

    def user_logged_in(request: Request, session, to: str = "/") -> RedirectResponse:
        return set_user_cookie(RedirectResponse(to, status_code=303), request, session)

    def set_user_cookie(response: Response, request: Request, session) -> Response:
        response.set_cookie(USER_COOKIE, users.cookie(session), max_age=USER_SESSION_SECONDS, httponly=True,
                            samesite="lax", secure=is_https(request))
        return response

    def too_many(error: TooManyTries) -> Response:
        minutes = math.ceil(error.retry_after / 60)
        return Response(f"Too many wrong tries, try again in {minutes} minute{'s' if minutes > 1 else ''}",
                        status_code=429, headers={"Retry-After": str(error.retry_after)})

    @router.get("/login", include_in_schema=False)
    def login_page(request: Request):
        if not auth.enabled or auth.valid_session(request.cookies.get(COOKIE)):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(login_html, headers={"Cache-Control": "no-cache"})

    @router.post("/login", include_in_schema=False)
    async def login(request: Request, username: str = Form(""), password: str = Form("")):
        if not auth.enabled:
            return RedirectResponse("/", status_code=303)
        ip = client_ip(request)
        name = username.strip().lower()
        session = None
        try:
            with password_tries.attempt(limit_key(ip)) as attempt, user_password_tries.attempt(name) as user_attempt:
                if multi and username != auth.username:
                    session = await with_password_check(users.login, username, password, ip,
                                                        countries.country(ip, request.headers))
                    ok = session is not None
                else:
                    ok = auth.check_credentials(username, password)
                if not ok:
                    attempt.wrong()
                    user_attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return RedirectResponse("/login?error=1", status_code=303)
        except TooManyTries as error:
            return too_many(error)
        password_tries.reset(limit_key(ip))
        user_password_tries.reset(name)
        if session is not None:
            if not await run_in_threadpool(users.totp_on, session.user_id):
                return user_logged_in(request, session)
            # Password right, and this user has a one-time code: the session waits for it
            response = RedirectResponse("/login/totp", status_code=303)
            response.set_cookie(USER_PENDING_COOKIE, users.cookie(session, pending=True), max_age=USER_PENDING_SECONDS,
                                httponly=True, samesite="lax", secure=is_https(request))
            return response
        if not totp:
            return logged_in(request)
        # Password right: now the one-time code (or adding the secret to the app, the first time)
        response = RedirectResponse("/login/totp", status_code=303)
        response.set_cookie(PENDING_COOKIE, auth.new_pending(), max_age=PENDING_SECONDS, httponly=True,
                            samesite="lax", secure=is_https(request))
        return response

    def pending_user(request: Request):
        """Multi-user: the user whose password was right and whose one-time code is still asked."""
        return users.session(request.cookies.get(USER_PENDING_COOKIE), pending=True) if multi else None

    @router.get("/login/totp", include_in_schema=False)
    def totp_page(request: Request):
        if pending_user(request) is not None:
            return HTMLResponse(totp_html.replace("{{SETUP}}", "").replace("{{MODE}}", "verify"),
                                headers={"Cache-Control": "no-store"})
        if not totp or not auth.valid_pending(request.cookies.get(PENDING_COOKIE)):
            return RedirectResponse("/login", status_code=303)
        # The secret is shown only until a first code proves it's in the authenticator app
        setup = "" if totp.confirmed else (
            f'<div class="totp-qr">{totp.qr_svg(auth.username)}</div>'
            f'<code class="totp-secret">{one_time.grouped(totp.secret)}</code>'
        )
        html = totp_html.replace("{{SETUP}}", setup).replace("{{MODE}}", "verify" if totp.confirmed else "setup")
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    @router.post("/login/totp", include_in_schema=False)
    async def totp_login(request: Request, code: str = Form("")):
        pending = await run_in_threadpool(pending_user, request)
        if pending is not None:
            ip = limit_key(client_ip(request))
            try:
                with code_tries.attempt(ip) as attempt:
                    if not await run_in_threadpool(users.verify_totp, pending.user_id, code):
                        attempt.wrong()
                        await asyncio.sleep(WRONG_PASSWORD_DELAY)
                        return RedirectResponse("/login/totp?error=1", status_code=303)
            except TooManyTries as error:
                return too_many(error)
            code_tries.reset(ip)
            response = user_logged_in(request, pending)
            response.delete_cookie(USER_PENDING_COOKIE)
            return response
        if not totp or not auth.valid_pending(request.cookies.get(PENDING_COOKIE)):
            return RedirectResponse("/login", status_code=303)
        ip = limit_key(client_ip(request))
        try:
            with code_tries.attempt(ip) as attempt:
                if not totp.verify(code):
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return RedirectResponse("/login/totp?error=1", status_code=303)
        except TooManyTries as error:
            return too_many(error)
        code_tries.reset(ip)
        if not totp.confirmed:
            totp.confirm()
        return logged_in(request)

    # POST only, so another website can't log you out with a link or an image
    @router.post("/logout", include_in_schema=False)
    def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE))
        response = RedirectResponse("/login" if auth.enabled else "/", status_code=303)
        response.delete_cookie(COOKIE)
        if multi:
            users.revoke(request.cookies.get(USER_COOKIE))
            response.delete_cookie(USER_COOKIE)
            visitor_demos.stop(request.cookies.get(DEMO_COOKIE))
            set_demo(response, request, False)
        return response

    # --- Multi-user mode: sign-up by invitation, passwords, admin ---

    def multi_only():
        if not multi:
            raise HTTPException(404, "Not found")

    def logged_in_notice(request: Request) -> str:
        """Who is already logged in in this browser: signing up logs them out here, so the page says it first."""
        session = users.session(request.cookies.get(USER_COOKIE))
        if session is None:
            return ""
        return (f'<p class="login-error"><span data-i18n="register.logged_in">{english["register.logged_in"]}</span> '
                f'<strong>{html.escape(session.username)}</strong>. <span data-i18n="register.logged_in_note">'
                f'{english["register.logged_in_note"]}</span></p>')

    def signup_page(request: Request, token: str, error: str = "", username: str = "", email: str = "") -> HTMLResponse:
        invitation = users.invitation(token)
        if invitation is None:
            response = fill(message_html, MESSAGE=message("register.error.invitation"))
            response.status_code = 404
            return response
        return fill(register_html, TOKEN=html.escape(token), ERROR=logged_in_notice(request) + error,
                    USERNAME=html.escape(username),
                    EMAIL=html.escape(invitation.email or email), EMAIL_READONLY="readonly" if invitation.email else "")

    @router.get("/invite/{token}", include_in_schema=False, dependencies=[Depends(multi_only)])
    def invite_page(request: Request, token: str):
        return signup_page(request, token)

    @router.post("/invite/{token}", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def sign_up(request: Request, token: str, username: str = Form(""), email: str = Form(""),
                      password: str = Form(""), password2: str = Form("")):
        if password != password2:
            return signup_page(request, token, message("register.error.mismatch"), username, email)
        try:
            ip = client_ip(request)
            session, recovery = await with_password_check(users.sign_up, token, username, email, password, ip,
                                                          countries.country(ip, request.headers))
        except SignupError as error:
            if error.code == "invitation":
                return signup_page(request, token)
            return signup_page(request, token, message(f"register.error.{error.code}"), username, email)
        # Whoever was logged in in this browser is logged out for good (the page said so)
        await run_in_threadpool(users.revoke, request.cookies.get(USER_COOKIE))
        return set_user_cookie(fill(recovery_html, KEY=html.escape(recovery)), request, session)

    @router.get("/recover", include_in_schema=False, dependencies=[Depends(multi_only)])
    def recover_page(error: str = ""):
        return fill(recover_html, ERROR=message("recover.error") if error else "")

    @router.post("/recover", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def recover(request: Request, username: str = Form(""), recovery_key: str = Form(""),
                      password: str = Form(""), password2: str = Form("")):
        if password != password2:
            return fill(recover_html, ERROR=message("register.error.mismatch"))
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                try:
                    session = await with_password_check(users.recover, username, recovery_key, password)
                except SignupError:
                    return fill(recover_html, ERROR=message("register.error.password"))
                if session is None:
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return fill(recover_html, ERROR=message("recover.error"))
        except TooManyTries as error:
            return too_many(error)
        return user_logged_in(request, session)

    def totp_section(request: Request, note: str = "") -> str:
        """The account page's one-time code: how to turn it on (a new secret each time), or a button to turn it off."""
        user = request.state.user
        if users.totp_on(user.user_id):
            return (f'<form method="post" action="/account/totp/off" class="login-box">'
                    f'<h2 data-i18n="account.totp">{english["account.totp"]}</h2>{note}'
                    f'<p class="totp-help" data-i18n="account.totp_on">{english["account.totp_on"]}</p>'
                    f'<label><span data-i18n="account.current_password">{english["account.current_password"]}</span> '
                    f'<input name="current" type="password" autocomplete="current-password" required></label>'
                    f'<button type="submit" data-i18n="account.totp_turn_off">{english["account.totp_turn_off"]}</button>'
                    f'</form>')
        secret = one_time.new_secret()
        return (f'<form method="post" action="/account/totp" class="login-box">'
                f'<h2 data-i18n="account.totp">{english["account.totp"]}</h2>{note}'
                f'<p class="totp-help" data-i18n="account.totp_off">{english["account.totp_off"]}</p>'
                f'<div class="totp-qr">{one_time.qr_svg(secret, user.username)}</div>'
                f'<code class="totp-secret">{one_time.grouped(secret)}</code>'
                f'<input type="hidden" name="secret" value="{secret}">'
                f'<label><span data-i18n="totp.code">{english["totp.code"]}</span> <input name="code" inputmode="numeric" '
                f'pattern="[0-9 ]*" maxlength="7" autocomplete="one-time-code" required></label>'
                f'<label><span data-i18n="account.current_password">{english["account.current_password"]}</span> '
                f'<input name="current" type="password" autocomplete="current-password" required></label>'
                f'<button type="submit" class="primary" data-i18n="account.totp_turn_on">{english["account.totp_turn_on"]}</button>'
                f'</form>')

    def account_page(request: Request, note: str = "", totp_note: str = "") -> HTMLResponse:
        return fill(account_html, USERNAME=html.escape(request.state.user.username), MESSAGE=note,
                    TOTP=totp_section(request, totp_note))

    @router.get("/account", include_in_schema=False, dependencies=[Depends(multi_only)])
    def account(request: Request, changed: bool = False, totp: str = ""):
        done = {"on": "account.totp_done_on", "off": "account.totp_done_off"}.get(totp)
        return account_page(request, message("account.changed", "login-ok") if changed else "",
                            message(done, "login-ok") if done else "")

    @router.post("/account/totp", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def turn_totp_on(request: Request, secret: str = Form(""), code: str = Form(""), current: str = Form("")):
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                problem = await with_password_check(users.set_totp, request.state.user.user_id, current, secret, code)
                if problem == "password":
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return account_page(request, totp_note=message("account.error"))
        except TooManyTries as error:
            return too_many(error)
        if problem == "code":
            return account_page(request, totp_note=message("totp.error"))
        return RedirectResponse("/account?totp=on", status_code=303)

    @router.post("/account/totp/off", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def turn_totp_off(request: Request, current: str = Form("")):
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                if not await with_password_check(users.remove_totp, request.state.user.user_id, current):
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return account_page(request, totp_note=message("account.error"))
        except TooManyTries as error:
            return too_many(error)
        return RedirectResponse("/account?totp=off", status_code=303)

    @router.post("/account/password", include_in_schema=False, dependencies=[Depends(multi_only)])
    async def change_password(request: Request, current: str = Form(""), password: str = Form(""),
                              password2: str = Form("")):
        if password != password2:
            return account_page(request, message("register.error.mismatch"))
        ip = limit_key(client_ip(request))
        try:
            with password_tries.attempt(ip) as attempt:
                try:
                    session = await with_password_check(users.change_password, request.state.user.user_id, current,
                                                        password)
                except SignupError:
                    return account_page(request, message("register.error.password"))
                if session is None:
                    attempt.wrong()
                    await asyncio.sleep(WRONG_PASSWORD_DELAY)
                    return account_page(request, message("account.error"))
        except TooManyTries as error:
            return too_many(error)
        return user_logged_in(request, session, "/account?changed=1")

    return router
