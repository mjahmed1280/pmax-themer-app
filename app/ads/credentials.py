"""Credential providers for the real backend."""
from __future__ import annotations

from pathlib import Path

import yaml

from app.ads.base import AdsError


class EnvCredentials:
    """Cloud Run: ONE fixed, dedicated Google Ads identity, injected as env vars from Secret Manager."""

    def __init__(self, s):
        self._cfg = {
            "developer_token": s.google_ads_developer_token,
            "client_id": s.google_ads_client_id,
            "client_secret": s.google_ads_client_secret,
            "refresh_token": s.google_ads_refresh_token,
            "use_proto_plus": True,
        }
        if s.google_ads_login_customer_id:
            self._cfg["login_customer_id"] = s.google_ads_login_customer_id.replace("-", "")

    def config(self) -> dict:
        return dict(self._cfg)

    def status(self) -> dict:
        return {"mode": "fixed-credentials", "connected": True,
                "detail": "Using the deployment's dedicated Google Ads credentials"}


class LocalYamlCredentials:
    """LOCAL DEV ONLY: one shared google-ads.yaml kept outside git."""

    def __init__(self, path: Path):
        self.path = path

    def config(self) -> dict:
        try:
            data = yaml.safe_load(self.path.read_text("utf-8")) or {}
        except FileNotFoundError:
            raise AdsError(f"google-ads.yaml not found at the configured path") from None
        if not data.get("developer_token"):
            raise AdsError("google-ads.yaml is missing developer_token")
        return data

    def status(self) -> dict:
        return {"mode": "local-yaml", "connected": self.path.exists(),
                "detail": "Using the shared local google-ads.yaml (dev only)"}
