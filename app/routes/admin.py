"""Multi-user mode: the admin's page (users, invitations, blocked emails)."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..deps import Context, is_https
from ..users import SignupError, expired

NEW_INVITE_COOKIE = "abt_new_invite"
TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "templates"),
                        autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def router(ctx: Context) -> APIRouter:
    router = APIRouter()
    settings, users, multi, pages = ctx.settings, ctx.users, ctx.multi, ctx.pages
    english = pages.english
    template = TEMPLATES.get_template("admin.html")

    def multi_only():
        if not multi:
            raise HTTPException(404, "Not found")

    def when(moment) -> str:
        return f"{moment:%Y-%m-%d %H:%M}" if moment else "—"

    def size(n: int) -> str:
        return f"{n / 1024:.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"

    def admin_page(new_link: str = "", error: str = "") -> HTMLResponse:
        """The page, from templates/admin.html (autoescaped); `error`: the text key of an error to show."""
        rows, total = [], 0
        for user, db_size in users.list_users():
            total += db_size
            days = (user.expires_at - datetime.now()).days if user.expires_at is not None else -1
            rows.append({
                "id": user.id, "username": user.username, "email": user.email, "created": when(user.created_at),
                "last_login": when(user.last_login_at),
                "where": " · ".join(x for x in (user.last_ip, user.last_country) if x), "size": size(db_size),
                "never_expires": user.expires_at is None, "expires": when(user.expires_at),
                "days_left": max(0, days) if days >= 0 else None,
                "expired": expired(user), "blocked": user.blocked, "totp": user.totp_secret is not None})
        invitations = [{"id": i.id, "email": i.email, "expires": when(i.expires_at)} for i in users.open_invitations()]
        blocked = [{"email": b.email, "created": when(b.created_at)} for b in users.blocked_emails()]
        page = template.render(en=english, asset_version=pages.asset_version, users=rows, total_size=size(total),
                               max_users=settings.max_users, default_days=settings.account_days or 30,
                               invitations=invitations, blocked=blocked, new_link=new_link, error=error)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @router.get("/admin", include_in_schema=False, dependencies=[Depends(multi_only)])
    def admin(request: Request, error: str = ""):
        # The new invitation's link comes once in a short-lived cookie (Post/Redirect/Get), so reloading the
        # page shows the list again instead of sending the form again and making another invitation
        token = request.cookies.get(NEW_INVITE_COOKIE, "")
        link = f"{str(request.base_url).rstrip('/')}/invite/{token}" if token else ""
        response = admin_page(link, "register.error.email" if error == "email" and not token else "")
        if token:
            response.delete_cookie(NEW_INVITE_COOKIE, path="/admin")
        return response

    @router.post("/admin/invitations", include_in_schema=False, dependencies=[Depends(multi_only)])
    def create_invitation(request: Request, email: str = Form("")):
        try:
            token = users.invite(email)
        except SignupError:
            return RedirectResponse("/admin?error=email", status_code=303)
        response = RedirectResponse("/admin", status_code=303)
        response.set_cookie(NEW_INVITE_COOKIE, token, max_age=60, path="/admin", httponly=True, samesite="strict",
                            secure=is_https(request))
        return response

    @router.post("/admin/invitations/{invitation_id}/cancel", include_in_schema=False, dependencies=[Depends(multi_only)])
    def cancel_invitation(invitation_id: int):
        users.cancel_invitation(invitation_id)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/block", include_in_schema=False, dependencies=[Depends(multi_only)])
    def block_user(user_id: int):
        users.set_blocked(user_id, True)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/unblock", include_in_schema=False, dependencies=[Depends(multi_only)])
    def unblock_user(user_id: int):
        users.set_blocked(user_id, False)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/extend", include_in_schema=False, dependencies=[Depends(multi_only)])
    def extend_user(user_id: int, days: int = Form(30, ge=1, le=3650)):
        users.extend(user_id, days)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/no-expiry", include_in_schema=False, dependencies=[Depends(multi_only)])
    def no_expiry(user_id: int):
        users.extend(user_id, None)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/totp/remove", include_in_schema=False, dependencies=[Depends(multi_only)])
    def remove_user_totp(user_id: int):
        """For a user who lost their authenticator app (and their recovery key): they log in with the password."""
        users.remove_totp(user_id)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/users/{user_id}/delete", include_in_schema=False, dependencies=[Depends(multi_only)])
    def delete_user(user_id: int, block_email: bool = False):
        users.delete_user(user_id, block_email)
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/blocked-emails", include_in_schema=False, dependencies=[Depends(multi_only)])
    def block_email(email: str = Form("")):
        try:
            users.block_email(email)
        except SignupError:
            return admin_page(error="register.error.email")
        return RedirectResponse("/admin", status_code=303)

    @router.post("/admin/blocked-emails/unblock", include_in_schema=False, dependencies=[Depends(multi_only)])
    def unblock_email(email: str = Query("")):
        users.unblock_email(email)
        return RedirectResponse("/admin", status_code=303)

    return router
