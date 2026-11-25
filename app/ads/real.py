"""Real Google Ads backend (google-ads client, GAQL from the notebook).

Credentials come from a CredentialProvider-style callable that returns a client config
dict. Locally that is the google-ads.yaml; in prod it will be the signed-in user's own
OAuth refresh token (see app/auth/oauth_ads.py).

SECURITY: config values are never logged or included in error messages.
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Callable

from app.ads.base import AdsError, AdsReauthError, ChunkResult

log = logging.getLogger("pmax.ads")

try:
    from google.auth.exceptions import RefreshError
except Exception:  # pragma: no cover
    class RefreshError(Exception):
        pass

REAUTH_MSG = "Your Google Ads access expired or was revoked. Please reconnect your Google account."
SEARCH_THEME_POLICY_VIOLATION = 3   # AssetGroupSignalErrorEnum value used by the notebook

_ALLOWED_CFG_KEYS = {
    "developer_token", "client_id", "client_secret", "refresh_token", "login_customer_id",
    "use_proto_plus", "json_key_file_path", "impersonated_email", "linked_customer_id",
    "endpoint", "http_proxy",
}


def _digits(value, what: str) -> str:
    s = str(value or "").replace("-", "").strip()
    if not s.isdigit():
        raise AdsError(f"Invalid {what}")
    return s


def _id_list(values, what: str) -> list[str]:
    return [_digits(v, what) for v in values or []]


def _date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except Exception:
        raise AdsError("Invalid date (expected YYYY-MM-DD)")


class RealBackend:
    def __init__(self, config_provider: Callable[[], dict]):
        self._config_provider = config_provider
        self._clients: dict[str, object] = {}
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- client
    def _client(self, login_customer_id: str | None):
        from google.ads.googleads.client import GoogleAdsClient

        key = login_customer_id or ""
        with self._lock:
            c = self._clients.get(key)
            if c is None:
                cfg = {k: v for k, v in self._config_provider().items() if k in _ALLOWED_CFG_KEYS}
                cfg["use_proto_plus"] = True
                if login_customer_id:
                    cfg["login_customer_id"] = _digits(login_customer_id, "login customer id")
                else:
                    cfg.pop("login_customer_id", None)
                c = GoogleAdsClient.load_from_dict(cfg)
                self._clients[key] = c
            return c

    def _search(self, client, customer_id: str, query: str):
        from google.ads.googleads.errors import GoogleAdsException

        svc = client.get_service("GoogleAdsService")
        try:
            return list(svc.search(customer_id=customer_id, query=query))
        except GoogleAdsException as ex:
            raise AdsError(self._summarize(ex)) from None
        except RefreshError:
            raise AdsReauthError(REAUTH_MSG) from None

    @staticmethod
    def _summarize(ex) -> str:
        msgs = [e.message for e in ex.failure.errors][:3]
        return "Google Ads API error: " + "; ".join(msgs) if msgs else "Google Ads API error"

    # ------------------------------------------------------------- accounts
    def list_accounts(self) -> list[dict]:
        from google.ads.googleads.errors import GoogleAdsException

        base = self._client(None)
        try:
            names = base.get_service("CustomerService").list_accessible_customers().resource_names
        except GoogleAdsException as ex:
            raise AdsError(self._summarize(ex)) from None
        except RefreshError:
            raise AdsReauthError(REAUTH_MSG) from None
        accessible = [n.split("/")[-1] for n in names]
        out: dict[str, dict] = {}
        for cid in accessible:
            try:
                client = self._client(cid)
                rows = self._search(client, cid, "SELECT customer.id, customer.descriptive_name, customer.manager FROM customer LIMIT 1")
            except AdsReauthError:
                raise
            except AdsError as e:
                log.info("skip accessible customer %s: %s", cid, e)
                continue
            if not rows:
                continue
            c = rows[0].customer
            entry = {"id": str(c.id), "name": c.descriptive_name or str(c.id), "is_manager": bool(c.manager),
                     "login_customer_id": cid, "parent": None}
            out.setdefault(entry["id"], entry)
            if c.manager:
                try:
                    kids = self._search(
                        client, cid,
                        "SELECT customer_client.id, customer_client.descriptive_name, customer_client.manager "
                        "FROM customer_client WHERE customer_client.level = 1 AND customer_client.status = 'ENABLED'")
                except AdsReauthError:
                    raise
                except AdsError as e:
                    log.info("could not expand MCC %s: %s", cid, e)
                    continue
                for r in kids:
                    k = r.customer_client
                    out[str(k.id)] = {"id": str(k.id), "name": k.descriptive_name or str(k.id),
                                      "is_manager": bool(k.manager), "login_customer_id": cid, "parent": cid}
        return sorted(out.values(), key=lambda a: (a["is_manager"] is False, a["name"].lower()))

    # --------------------------------------------------------------- reads
    def list_pmax_asset_groups(self, customer_id, login_customer_id):
        cid = _digits(customer_id, "customer id")
        rows = self._search(self._client(login_customer_id), cid, """
            SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group.status
            FROM asset_group
            WHERE campaign.advertising_channel_type = 'PERFORMANCE_MAX'
              AND campaign.status = 'ENABLED'
              AND asset_group.status != 'REMOVED'""")
        return [{"customer_id": cid, "campaign_id": str(r.campaign.id), "campaign_name": r.campaign.name,
                 "asset_group_id": str(r.asset_group.id), "asset_group_name": r.asset_group.name,
                 "status": r.asset_group.status.name} for r in rows]

    def list_search_structure(self, customer_id, login_customer_id):
        cid = _digits(customer_id, "customer id")
        rows = self._search(self._client(login_customer_id), cid, """
            SELECT campaign.id, campaign.name, ad_group.id, ad_group.name
            FROM ad_group
            WHERE campaign.advertising_channel_type = 'SEARCH'
              AND campaign.status = 'ENABLED'
              AND ad_group.status = 'ENABLED'
              AND ad_group.type = 'SEARCH_STANDARD'""")
        camps: dict[str, dict] = {}
        for r in rows:
            c = camps.setdefault(str(r.campaign.id), {"campaign_id": str(r.campaign.id),
                                                      "campaign_name": r.campaign.name, "ad_groups": []})
            c["ad_groups"].append({"ad_group_id": str(r.ad_group.id), "ad_group_name": r.ad_group.name})
        return sorted(camps.values(), key=lambda c: c["campaign_name"].lower())

    def list_keywords(self, customer_id, login_customer_id, campaign_ids, ad_group_ids, date_from, date_to, min_clicks):
        cid = _digits(customer_id, "customer id")
        where = [
            "ad_group.type = 'SEARCH_STANDARD'",
            "campaign.status = 'ENABLED'",
            "ad_group_criterion.status = 'ENABLED'",
            "ad_group_criterion.negative = FALSE",     # notebook did not exclude negatives
            f"metrics.clicks >= {max(int(min_clicks), 0)}",
            f"segments.date BETWEEN '{_date(date_from)}' AND '{_date(date_to)}'",
        ]
        camp = _id_list(campaign_ids, "campaign id")
        ag = _id_list(ad_group_ids, "ad group id")
        if camp:
            where.append(f"campaign.id IN ({', '.join(camp)})")
        if ag:
            where.append(f"ad_group.id IN ({', '.join(ag)})")
        rows = self._search(self._client(login_customer_id), cid, f"""
            SELECT campaign.id, campaign.name, ad_group.id, ad_group.name,
                   ad_group_criterion.keyword.text, metrics.clicks, metrics.conversions, metrics.cost_micros
            FROM keyword_view
            WHERE {' AND '.join(where)}""")
        return [{"campaign_id": str(r.campaign.id), "campaign_name": r.campaign.name,
                 "ad_group_id": str(r.ad_group.id), "ad_group_name": r.ad_group.name,
                 "keyword": r.ad_group_criterion.keyword.text, "clicks": int(r.metrics.clicks),
                 "conversions": round(r.metrics.conversions, 2),
                 "cost": round(r.metrics.cost_micros / 1_000_000, 2)} for r in rows]

    def describe_asset_group(self, customer_id, login_customer_id, asset_group_id):
        cid = _digits(customer_id, "customer id")
        agid = _digits(asset_group_id, "asset group id")
        client = self._client(login_customer_id)
        rows = self._search(client, cid, f"""
            SELECT asset_group.id, asset_group.name, asset_group.final_urls, asset_group.final_mobile_urls,
                   asset_group.path1, asset_group.path2,
                   asset_group_asset.field_type, asset.resource_name
            FROM asset_group_asset
            WHERE asset_group.id = {agid}
              AND asset_group_asset.status = 'ENABLED'
              AND asset.source != 'AUTOMATICALLY_CREATED'""")
        if not rows:
            raise AdsError("Source asset group has no copyable assets (or was not found)")
        ag = rows[0].asset_group
        assets = []
        for r in rows:
            ft = r.asset_group_asset.field_type.name
            if ft in ("UNSPECIFIED", "UNKNOWN"):
                continue
            assets.append({"field_type": ft, "resource_name": r.asset.resource_name})
        return {
            "name": ag.name,
            "final_urls": list(ag.final_urls),
            "final_mobile_urls": list(ag.final_mobile_urls),
            "path1": ag.path1, "path2": ag.path2,
            "assets": assets,
            "audiences": self._customer_scoped_audiences(client, cid, agid),
        }

    def _customer_scoped_audiences(self, client, cid: str, agid: str) -> list[str]:
        rows = self._search(client, cid,
                            f"SELECT asset_group_signal.audience.audience FROM asset_group_signal "
                            f"WHERE asset_group.id = {agid}")
        names = [r.asset_group_signal.audience.audience for r in rows if r.asset_group_signal.audience.audience]
        if not names:
            return []
        clause = ", ".join(f"'{n}'" for n in names if n.startswith(f"customers/{cid}/audiences/"))
        if not clause:
            return []
        # asset-group-scoped audiences cannot be reused in another asset group
        rows = self._search(client, cid, f"SELECT audience.resource_name FROM audience "
                                         f"WHERE audience.resource_name IN ({clause}) AND audience.scope = 'CUSTOMER'")
        return [r.audience.resource_name for r in rows]

    # -------------------------------------------------------------- writes
    def create_asset_group(self, customer_id, login_customer_id, campaign_id, source, name, themes,
                           copy_audiences, validate_only) -> ChunkResult:
        from google.ads.googleads.errors import GoogleAdsException

        cid = _digits(customer_id, "customer id")
        camp = _digits(campaign_id, "campaign id")
        client = self._client(login_customer_id)
        ag_svc = client.get_service("AssetGroupService")
        camp_svc = client.get_service("CampaignService")
        temp_rn = ag_svc.asset_group_path(cid, "-1")

        ops = []
        op = client.get_type("MutateOperation")
        ag = op.asset_group_operation.create
        ag.resource_name = temp_rn
        ag.name = name
        ag.campaign = camp_svc.campaign_path(cid, camp)
        ag.status = client.enums.AssetGroupStatusEnum.PAUSED      # ALWAYS paused
        if source.get("final_urls"):
            ag.final_urls.append(source["final_urls"][0])
        if source.get("final_mobile_urls"):
            ag.final_mobile_urls.append(source["final_mobile_urls"][0])
        if source.get("path1"):
            ag.path1 = source["path1"]
        if source.get("path2"):
            ag.path2 = source["path2"]
        ops.append(op)

        for a in source.get("assets", []):
            op = client.get_type("MutateOperation")
            aga = op.asset_group_asset_operation.create
            aga.asset_group = temp_rn
            aga.asset = a["resource_name"]
            aga.field_type = getattr(client.enums.AssetFieldTypeEnum, a["field_type"])
            ops.append(op)

        if copy_audiences:
            for aud in source.get("audiences", []):
                op = client.get_type("MutateOperation")
                sig = op.asset_group_signal_operation.create
                sig.asset_group = temp_rn
                sig.audience.audience = aud
                ops.append(op)

        for t in themes:
            op = client.get_type("MutateOperation")
            sig = op.asset_group_signal_operation.create
            sig.asset_group = temp_rn
            sig.search_theme.text = t
            ops.append(op)

        request = client.get_type("MutateGoogleAdsRequest")
        request.customer_id = cid
        request.mutate_operations.extend(ops)
        request.validate_only = bool(validate_only)
        try:
            resp = client.get_service("GoogleAdsService").mutate(request=request)
        except RefreshError:
            raise AdsReauthError(REAUTH_MSG) from None
        except GoogleAdsException as ex:
            banned, other = [], []
            for e in ex.failure.errors:
                if e.error_code.asset_group_signal_error == SEARCH_THEME_POLICY_VIOLATION:
                    policy = ""
                    try:
                        policy = e.details.policy_violation_details.key.policy_name
                    except Exception:
                        pass
                    banned.append({"theme": e.trigger.string_value, "policy": policy})
                else:
                    other.append(e.message)
            if banned and not other:
                return ChunkResult(ok=False, banned=banned)
            raise AdsError("Google Ads API error: " + "; ".join(other[:3] or ["unknown"])) from None

        if validate_only:
            return ChunkResult(ok=True)
        for r in resp.mutate_operation_responses:
            if r._pb.HasField("asset_group_result"):
                rn = r.asset_group_result.resource_name
                return ChunkResult(ok=True, resource_name=rn, asset_group_id=rn.split("/")[-1])
        raise AdsError("Mutate succeeded but no asset group result was returned")
