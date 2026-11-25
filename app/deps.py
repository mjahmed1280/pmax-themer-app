"""Wiring + request dependencies."""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, HTTPException, Request

from app.ads.base import AdsBackend
from app.ads.credentials import EnvCredentials, LocalYamlCredentials
from app.ads.mock import MockBackend
from app.ads.real import RealBackend
from app.audit import AuditLog
from app.auth.access import AccessPolicy, FileAccessStore
from app.auth.base import User
from app.auth.google_oauth import GoogleOAuth, TokenCrypto, UserBackends
from app.auth.static_login import StaticLoginProvider
from app.config import Settings


@dataclass
class Container:
    settings: Settings
    auth: object
    access: AccessPolicy
    backend: AdsBackend
    audit: AuditLog
    creds_status: dict
    oauth: GoogleOAuth | None = None
    backends: UserBackends | None = None
    crypto: TokenCrypto | None = None


def build_container(s: Settings) -> Container:
    s.validate_runtime()
    data = Path(s.data_dir)
    access = AccessPolicy(FileAccessStore(data / "access.json"), s.admin_list,
                          s.allowed_email_list + list(s.login_users), s.allowed_domain_list)
    auth = StaticLoginProvider(s)
    if s.ads_backend == "mock":
        backend: AdsBackend = MockBackend()
        creds_status = {"mode": "mock", "connected": True, "detail": "Mock data - no Google Ads API calls"}
    else:
        creds = EnvCredentials(s) if s.env_creds_complete else LocalYamlCredentials(s.yaml_path)
        backend = RealBackend(creds.config)
        creds_status = creds.status()
    c = Container(s, auth, access, backend, AuditLog(data / "audit.jsonl"), creds_status)
    if s.ads_access == "user":
        c.oauth = GoogleOAuth(s.google_ads_client_id, s.google_ads_client_secret)
        c.crypto = TokenCrypto(s.secret_key)
        if s.ads_backend == "real":
            c.backends = UserBackends(s, c.crypto)
    return c


def container(request: Request) -> Container:
    return request.app.state.c


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def _user_or_none(request: Request) -> tuple[User | None, bool]:
    """-> (user, authenticated). authenticated=True but user None means 'not allowed'."""
    c = container(request)
    ident = c.auth.current_identity(request)
    if not ident:
        return None, False
    return c.access.resolve(ident[0], ident[1]), True


def api_user(request: Request) -> User:
    user, authed = _user_or_none(request)
    if not authed:
        if needs_connect(request):
            raise HTTPException(409, "Connect your Google Ads account to continue")
        raise HTTPException(401, "Not signed in")
    if user is None:
        raise HTTPException(403, "Your account is not allowed to use this deployment. Ask the app admin for access.")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent, request.session.get("csrf", "")):
            raise HTTPException(403, "Bad CSRF token")
    return user


def admin_user(request: Request) -> User:
    user = api_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Admin only")
    return user


def needs_connect(request: Request) -> bool:
    return getattr(container(request).auth, "needs_connect", lambda r: False)(request)


def user_ads(request: Request, user: User = Depends(api_user)) -> AdsBackend:
    """The Ads backend acting as THIS user (own refresh token), or the shared one."""
    c = container(request)
    if c.backends is None:
        return c.backend
    return c.backends.for_session(request.session, user.email)
