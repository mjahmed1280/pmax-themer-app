"""Static username/password login. LOCAL TESTING ONLY (see Settings.validate_runtime)."""
from __future__ import annotations

import secrets
import time

from fastapi import Request

from app.config import Settings

_FAILS: dict[str, list[float]] = {}
_WINDOW, _MAX_FAILS = 300, 8


class StaticLoginProvider:
    def __init__(self, settings: Settings):
        self.s = settings

    def current_identity(self, request: Request) -> tuple[str, str] | None:
        email = request.session.get("email")
        return (email, request.session.get("name", "")) if email else None

    # --- login helpers ----------------------------------------------
    def _throttled(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in _FAILS.get(key, []) if now - t < _WINDOW]
        _FAILS[key] = recent
        return len(recent) >= _MAX_FAILS

    def attempt(self, request: Request, username: str, password: str) -> bool:
        key = request.client.host if request.client else "?"
        if self._throttled(key):
            return False
        ok = secrets.compare_digest(username.encode(), self.s.app_user.encode()) & \
            secrets.compare_digest(password.encode(), self.s.app_password.encode())
        if not ok:
            _FAILS.setdefault(key, []).append(time.time())
            return False
        request.session.clear()
        request.session["gate"] = True        # passed the shared login
        if self.s.ads_access == "shared":
            # Shared identity maps to the first admin email so the admin UI works.
            request.session.update({"email": (self.s.admin_list or ["demo@local"])[0], "name": self.s.app_user})
        # user mode: the identity comes from the Google consent step (/connect)
        return True

    def needs_connect(self, request: Request) -> bool:
        return (self.s.ads_access == "user" and bool(request.session.get("gate"))
                and not request.session.get("email"))

    @staticmethod
    def logout(request: Request) -> None:
        request.session.clear()
