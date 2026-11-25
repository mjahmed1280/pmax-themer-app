"""Per-user Google Ads consent: /connect, /oauth/start, /oauth/callback (ADS_ACCESS=user)."""
from __future__ import annotations

import secrets
from pathlib import Path
from urllib.parse import quote_plus

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import container, csrf_token

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _ctx(request: Request, **extra):
    c = container(request)
    return {"request": request, "org": c.settings.org_name, "env": c.settings.env,
            "backend": c.settings.ads_backend, "allow_mutate": c.settings.allow_mutate,
            "csrf": csrf_token(request), **extra}


def _enabled(request: Request) -> bool:
    return container(request).oauth is not None


def _redirect_uri(request: Request) -> str:
    s = container(request).settings
    return s.oauth_redirect_uri or str(request.url_for("oauth_callback"))


@router.get("/connect", response_class=HTMLResponse)
def connect_page(request: Request, error: str | None = None):
    if not _enabled(request) or not request.session.get("gate"):
        return RedirectResponse("/login" if not request.session.get("gate") else "/", 303)
    if request.session.get("email") and request.session.get("ads_rt") and not error:
        return RedirectResponse("/", 303)
    return templates.TemplateResponse(request, "connect.html", _ctx(request, error=error))


@router.get("/oauth/start")
def oauth_start(request: Request):
    if not _enabled(request) or not request.session.get("gate"):
        return RedirectResponse("/login", 303)
    c = container(request)
    state, verifier = c.oauth.new_state()
    request.session["oauth_state"], request.session["oauth_verifier"] = state, verifier
    return RedirectResponse(c.oauth.auth_url(_redirect_uri(request), state, verifier), 303)


@router.get("/oauth/callback", name="oauth_callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    if not _enabled(request) or not request.session.get("gate"):
        return RedirectResponse("/login", 303)
    c = container(request)
    want = request.session.pop("oauth_state", "")
    verifier = request.session.pop("oauth_verifier", "")
    if error:
        return RedirectResponse("/connect?error=Google+access+was+not+granted.+Please+try+again.", 303)
    if not code or not want or not secrets.compare_digest(state, want):
        return RedirectResponse("/connect?error=The+sign-in+session+expired.+Please+try+again.", 303)
    try:
        grant = c.oauth.exchange(code, _redirect_uri(request), verifier)
    except ValueError as e:
        return RedirectResponse("/connect?error=" + quote_plus(str(e)), 303)

    user = c.access.resolve(grant["email"], grant["name"])
    if user is None:
        # Not on this deployment's allow-list: do not keep their token.
        c.oauth.revoke(grant["refresh_token"])
        c.audit.write(grant["email"], "connect_denied")
        return templates.TemplateResponse(request, "denied.html", _ctx(request), status_code=403)

    request.session["email"] = user.email
    request.session["name"] = user.name
    request.session["ads_rt"] = c.crypto.encrypt(grant["refresh_token"])
    c.audit.write(user.email, "connect")
    return RedirectResponse("/", 303)
