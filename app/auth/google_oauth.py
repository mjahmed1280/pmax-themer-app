"""Per-user Google Ads access via the Google OAuth consent screen.

Flow (after the shared basic login):
  /connect -> /oauth/start -> Google consent (adwords scope) -> /oauth/callback
The callback verifies the Google id_token (this gives the user's VERIFIED email), applies the
deployment's access policy to it, and stores the user's refresh token ENCRYPTED inside their own
signed session cookie (Fernet key derived from SECRET_KEY). No server-side token database.
Every Ads call then runs with that user's refresh token, so they only see what they can access.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from collections import OrderedDict
from urllib.parse import urlencode

from cryptography.fernet import Fernet, InvalidToken

from app.ads.base import AdsReauthError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ADWORDS = "https://www.googleapis.com/auth/adwords"
SCOPES = f"openid email profile {ADWORDS}"


class TokenCrypto:
    def __init__(self, secret_key: str):
        key = base64.urlsafe_b64encode(hashlib.sha256(f"{secret_key}|ads-token".encode()).digest())
        self._f = Fernet(key)

    def encrypt(self, token: str) -> str:
        return self._f.encrypt(token.encode()).decode()

    def decrypt(self, blob: str) -> str:
        try:
            return self._f.decrypt(blob.encode()).decode()
        except InvalidToken:
            raise AdsReauthError("Your Google Ads connection is invalid. Please reconnect.") from None


class GoogleOAuth:
    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id, client_secret

    @staticmethod
    def new_state() -> tuple[str, str]:
        """-> (state, pkce_verifier)"""
        return secrets.token_urlsafe(24), secrets.token_urlsafe(48)

    def auth_url(self, redirect_uri: str, state: str, verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code",
             "scope": SCOPES, "access_type": "offline", "prompt": "consent select_account", "state": state,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        return AUTH_URL + "?" + urlencode(q)

    def exchange(self, code: str, redirect_uri: str, verifier: str) -> dict:
        """-> {'email', 'name', 'refresh_token'}; raises ValueError with a user-safe message."""
        import requests
        from google.auth.transport import requests as greq
        from google.oauth2 import id_token

        r = requests.post(TOKEN_URL, data={
            "code": code, "client_id": self.client_id, "client_secret": self.client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code", "code_verifier": verifier}, timeout=15)
        if r.status_code != 200:
            raise ValueError("Google rejected the sign-in. Please try again.")
        tok = r.json()
        if not tok.get("refresh_token"):
            raise ValueError("Google did not grant offline Google Ads access. Please try again and accept all permissions.")
        if ADWORDS not in tok.get("scope", ""):
            raise ValueError("The Google Ads permission was not granted. Please try again and tick it.")
        try:
            claims = id_token.verify_oauth2_token(tok["id_token"], greq.Request(), self.client_id)
        except Exception:
            raise ValueError("Could not verify your Google identity. Please try again.") from None
        if not claims.get("email_verified"):
            raise ValueError("Your Google email is not verified.")
        return {"email": claims["email"].lower(), "name": claims.get("name", ""),
                "refresh_token": tok["refresh_token"]}

    def revoke(self, refresh_token: str) -> None:
        import requests
        try:
            requests.post(REVOKE_URL, params={"token": refresh_token}, timeout=10)
        except Exception:
            pass   # best effort; the token is dropped from the session regardless


class UserBackends:
    """Builds (and briefly caches) a RealBackend per connected user."""

    def __init__(self, settings, crypto: TokenCrypto, max_cached: int = 32):
        self.s, self.crypto, self.max = settings, crypto, max_cached
        self._cache: OrderedDict[str, object] = OrderedDict()

    def for_session(self, session, email: str):
        from app.ads.real import RealBackend

        blob = session.get("ads_rt")
        if not blob:
            raise AdsReauthError("Connect your Google Ads account to continue.")
        rt = self.crypto.decrypt(blob)
        key = hashlib.sha256(f"{email}|{rt}".encode()).hexdigest()
        be = self._cache.get(key)
        if be is None:
            cfg = {"developer_token": self.s.google_ads_developer_token, "client_id": self.s.google_ads_client_id,
                   "client_secret": self.s.google_ads_client_secret, "refresh_token": rt}
            be = RealBackend(lambda: dict(cfg))
            self._cache[key] = be
            while len(self._cache) > self.max:
                self._cache.popitem(last=False)
        self._cache.move_to_end(key)
        return be
