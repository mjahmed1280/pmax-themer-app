"""HTML pages: login, wizard, admin."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import _user_or_none, container, csrf_token, needs_connect

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _ctx(request: Request, **extra):
    c = container(request)
    return {"request": request, "org": c.settings.org_name, "env": c.settings.env,
            "backend": c.settings.ads_backend, "allow_mutate": c.settings.allow_mutate,
            "csrf": csrf_token(request), **extra}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    c = container(request)
    return templates.TemplateResponse(request, "login.html", _ctx(
        request, error=None, hint=(c.settings.auth_mode == "static" and c.settings.env == "local"),
        hint_user=c.settings.app_user, hint_pass=c.settings.app_password))


@router.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...), csrf: str = Form("")):
    c = container(request)
    sent_ok = secrets.compare_digest(csrf, request.session.get("csrf", "x")) if csrf else False
    if not sent_ok or not c.auth.attempt(request, username, password):
        return templates.TemplateResponse(request, "login.html", _ctx(
            request, error="Invalid username or password.", hint=(c.settings.auth_mode == "static" and c.settings.env == "local"),
            hint_user=c.settings.app_user, hint_pass=c.settings.app_password), status_code=401)
    ident = c.auth.current_identity(request)
    c.audit.write(ident[0] if ident else "shared-login", "login")
    return RedirectResponse("/", 303)


@router.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    if csrf and secrets.compare_digest(csrf, request.session.get("csrf", "x")):
        request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    user, authed = _user_or_none(request)
    if not authed:
        return RedirectResponse("/connect" if needs_connect(request) else "/login", 303)
    if user is None:
        return templates.TemplateResponse(request, "denied.html", _ctx(request), status_code=403)
    return templates.TemplateResponse(request, "app.html", _ctx(request, user=user.to_dict()))


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user, authed = _user_or_none(request)
    if not authed:
        return RedirectResponse("/connect" if needs_connect(request) else "/login", 303)
    if user is None or not user.is_admin:
        return templates.TemplateResponse(request, "denied.html", _ctx(request), status_code=403)
    return templates.TemplateResponse(request, "admin.html", _ctx(request, user=user.to_dict()))
