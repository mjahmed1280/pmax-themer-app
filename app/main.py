"""PMax Themer web app entrypoint.

Run locally:  python -m uvicorn app.main:app --reload --port 8080
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.ads.base import AdsError, AdsReauthError
from app.config import Settings, get_settings
from app.deps import build_container
from app.routers import api, oauth, pages

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def create_app(settings: Settings | None = None) -> FastAPI:
    s = settings or get_settings()
    app = FastAPI(title="PMax Themer", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.c = build_container(s)

    app.add_middleware(
        SessionMiddleware, secret_key=s.secret_key, session_cookie="pmax_session",
        same_site="lax", https_only=(s.env != "local"), max_age=8 * 3600)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store" if request.url.path.startswith(("/api", "/login")) else resp.headers.get("Cache-Control", "no-cache")
        return resp

    @app.exception_handler(AdsReauthError)
    async def ads_reauth(_req: Request, exc: AdsReauthError):
        return JSONResponse({"detail": str(exc)}, status_code=409)   # UI sends the user to /connect

    @app.exception_handler(AdsError)
    async def ads_error(_req: Request, exc: AdsError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
    app.include_router(pages.router)
    app.include_router(oauth.router)
    app.include_router(api.router)
    return app


def _lazy_app() -> FastAPI:
    return create_app()


app = _lazy_app() if __name__ != "__main__" else None
