"""Backend interface shared by the mock and real Google Ads implementations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AdsError(Exception):
    """Non-recoverable Ads API / validation error (message is safe to show the user)."""


class AdsReauthError(AdsError):
    """The user's Google Ads connection is missing/expired/revoked: send them to /connect."""


@dataclass
class ChunkResult:
    ok: bool
    resource_name: str | None = None        # None for validate_only
    asset_group_id: str | None = None
    banned: list[dict] = field(default_factory=list)   # [{"theme":..., "policy":...}]


class AdsBackend(Protocol):
    def list_accounts(self) -> list[dict]: ...

    def list_pmax_asset_groups(self, customer_id: str, login_customer_id: str | None) -> list[dict]: ...

    def list_search_structure(self, customer_id: str, login_customer_id: str | None) -> list[dict]: ...

    def list_keywords(
        self,
        customer_id: str,
        login_customer_id: str | None,
        campaign_ids: list[str],
        ad_group_ids: list[str],
        date_from: str,
        date_to: str,
        min_clicks: int,
    ) -> list[dict]: ...

    def describe_asset_group(self, customer_id: str, login_customer_id: str | None, asset_group_id: str) -> dict:
        """-> {name, final_urls, final_mobile_urls, path1, path2, assets:[{field_type, resource_name}], audiences:[resource_name]}"""

    def create_asset_group(
        self,
        customer_id: str,
        login_customer_id: str | None,
        campaign_id: str,
        source: dict,
        name: str,
        themes: list[str],
        copy_audiences: bool,
        validate_only: bool,
    ) -> ChunkResult:
        """Create ONE paused asset group with its assets/audiences/themes atomically.
        If themes are rejected by policy return ChunkResult(ok=False, banned=[...]).
        Raise AdsError for any other failure. With validate_only=True nothing is written."""
