"""Per-user Google Ads credentials (STUB - completed in the GCP phase).

Production model (option A):
  1. IAP verifies the user's identity (email).
  2. First use: user clicks "Connect Google Ads" -> OAuth web-server flow with
     scope https://www.googleapis.com/auth/adwords, access_type=offline, prompt=consent.
  3. The returned refresh token is encrypted (Cloud KMS) and stored per
     (deployment, email) in Firestore.
  4. Each Ads call builds a GoogleAdsClient with:
        developer_token  (Secret Manager, per deployment)
        client_id/secret (Secret Manager, per deployment OAuth client)
        refresh_token    (this user's)
        login_customer_id (MCC the user selected)
  5. The OAuth email must equal the IAP email, or the connection is rejected.

The deployment's tech admin owns the developer token / OAuth client, and controls
who may use the app via app.auth.access.AccessPolicy.

Interface the Ads layer depends on:
"""
from __future__ import annotations

from typing import Protocol


class CredentialProvider(Protocol):
    def config_for(self, email: str, login_customer_id: str | None) -> dict | None:
        """Return google-ads client config dict for this user, or None if not connected."""

    def status(self, email: str) -> dict:
        """{'connected': bool, 'mode': str, 'detail': str} for the UI."""
