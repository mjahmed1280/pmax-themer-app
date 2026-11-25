"""Application settings (env / .env driven)."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


def _csv(v) -> list[str]:
    if isinstance(v, list):
        return v
    return [x.strip().lower() for x in str(v or "").split(",") if x.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    env: str = "local"                 # local | prod
    auth_mode: str = "static"          # static (local demo) | basic (Cloud Run)
    ads_backend: str = "mock"          # mock | real
    allow_mutate: bool = False         # real writes disabled by default

    # shared = everyone uses the one fixed Google Ads identity (google-ads.yaml / GOOGLE_ADS_* env)
    # user   = after login each person grants their OWN Google Ads access (Google consent screen)
    ads_access: str = "shared"
    oauth_redirect_uri: str = ""       # optional; default = <request host>/oauth/callback

    app_user: str = "demo"
    app_password: str = "demo123"
    secret_key: str = ""

    org_name: str = "Local"
    admin_emails: str = "demo@local"
    allowed_emails: str = ""
    allowed_domains: str = ""

    google_ads_yaml_path: str = "../google-ads.yaml"

    # --- fixed Google Ads credentials for the real backend on Cloud Run ---
    # (inject the secrets via Secret Manager -> env vars; locally the yaml is used instead)
    google_ads_developer_token: str = ""
    google_ads_client_id: str = ""
    google_ads_client_secret: str = ""
    google_ads_refresh_token: str = ""
    google_ads_login_customer_id: str = ""
    # The whole downloaded OAuth client JSON ({"web": {...}}), e.g. a Secret Manager secret mounted as env.
    google_oauth_client_json: str = ""
    # ONE secret holding all fixed Ads credentials as JSON:
    # {"developer_token":"..","client_id":"..","client_secret":"..","refresh_token":"..","login_customer_id":"optional"}
    google_ads_credentials_json: str = ""

    data_dir: str = str(BASE_DIR / "data")

    @model_validator(mode="after")
    def _load_client_json(self):
        if self.google_ads_credentials_json.strip():
            import json
            try:
                d = json.loads(self.google_ads_credentials_json)
                for field, key in (("google_ads_developer_token", "developer_token"), ("google_ads_client_id", "client_id"),
                                   ("google_ads_client_secret", "client_secret"), ("google_ads_refresh_token", "refresh_token"),
                                   ("google_ads_login_customer_id", "login_customer_id")):
                    if d.get(key) and not getattr(self, field):
                        setattr(self, field, str(d[key]))
            except Exception:
                raise ValueError("GOOGLE_ADS_CREDENTIALS_JSON is not valid JSON") from None
        if self.google_oauth_client_json.strip() and not (self.google_ads_client_id and self.google_ads_client_secret):
            import json
            try:
                raw = json.loads(self.google_oauth_client_json)
                blk = raw.get("web") or raw.get("installed") or raw
                self.google_ads_client_id = self.google_ads_client_id or blk["client_id"]
                self.google_ads_client_secret = self.google_ads_client_secret or blk["client_secret"]
            except Exception:
                raise ValueError("GOOGLE_OAUTH_CLIENT_JSON is not a valid OAuth client JSON") from None
        return self

    @field_validator("env", "auth_mode", "ads_backend", "ads_access")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def admin_list(self) -> list[str]:
        return _csv(self.admin_emails)

    @property
    def allowed_email_list(self) -> list[str]:
        return _csv(self.allowed_emails)

    @property
    def allowed_domain_list(self) -> list[str]:
        return _csv(self.allowed_domains)

    @property
    def yaml_path(self) -> Path:
        p = Path(self.google_ads_yaml_path)
        return p if p.is_absolute() else (BASE_DIR / p).resolve()

    @property
    def env_creds_complete(self) -> bool:
        return all((self.google_ads_developer_token, self.google_ads_client_id,
                    self.google_ads_client_secret, self.google_ads_refresh_token))

    def validate_runtime(self) -> None:
        """Fail fast on unsafe combinations."""
        on_cloud_run = bool(os.environ.get("K_SERVICE"))
        if self.auth_mode not in ("static", "basic"):
            raise RuntimeError(f"Unknown AUTH_MODE {self.auth_mode!r} (use static or basic)")
        if self.ads_backend not in ("mock", "real"):
            raise RuntimeError(f"Unknown ADS_BACKEND {self.ads_backend!r}")
        if self.ads_access not in ("shared", "user"):
            raise RuntimeError(f"Unknown ADS_ACCESS {self.ads_access!r} (use shared or user)")
        if self.ads_access == "user":
            need = [n for n, v in (("SECRET_KEY", self.secret_key), ("GOOGLE_ADS_CLIENT_ID", self.google_ads_client_id),
                                   ("GOOGLE_ADS_CLIENT_SECRET", self.google_ads_client_secret)) if not v]
            if self.ads_backend == "real" and not self.google_ads_developer_token:
                need.append("GOOGLE_ADS_DEVELOPER_TOKEN")
            if need:
                raise RuntimeError("ADS_ACCESS=user needs: " + ", ".join(need))
        if self.auth_mode == "static" and (on_cloud_run or self.env != "local"):
            raise RuntimeError(
                "AUTH_MODE=static (demo/demo123 hint login) is local only. "
                "On Cloud Run use AUTH_MODE=basic with your own APP_USER/APP_PASSWORD.")
        if self.auth_mode == "basic":
            problems = []
            if not self.secret_key:
                problems.append("SECRET_KEY must be set")
            if self.app_password in ("", "demo123") or len(self.app_password) < 12:
                problems.append("APP_PASSWORD must be set to a non-default value of 12+ characters")
            if self.admin_list in ([], ["demo@local"]):
                problems.append("ADMIN_EMAILS must be set to a real address")
            if problems:
                raise RuntimeError("AUTH_MODE=basic: " + "; ".join(problems))
        if (self.ads_backend == "real" and self.ads_access == "shared"
                and (on_cloud_run or self.env != "local") and not self.env_creds_complete):
            raise RuntimeError("ADS_BACKEND=real in prod needs GOOGLE_ADS_DEVELOPER_TOKEN, GOOGLE_ADS_CLIENT_ID, "
                               "GOOGLE_ADS_CLIENT_SECRET and GOOGLE_ADS_REFRESH_TOKEN")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # Random key = fine for one local process. basic mode refuses to start without a fixed one.
    if not s.secret_key and s.auth_mode == "static" and s.ads_access == "shared":
        s.secret_key = secrets.token_urlsafe(32)
    return s
