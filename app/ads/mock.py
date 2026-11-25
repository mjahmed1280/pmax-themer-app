"""Fake Ads backend for building/testing the UI with zero API calls and zero real data."""
from __future__ import annotations

import itertools
import random
import zlib
from threading import Lock

from app.ads.base import AdsError, ChunkResult

_BANNED_WORDS = {"guaranteed returns": "Misleading financial claims (mock)"}

_MCC = {"id": "1000000001", "name": "Demo Agency MCC", "is_manager": True, "login_customer_id": "1000000001", "parent": None}
_ACCOUNTS = [
    _MCC,
    {"id": "2000000001", "name": "Demo Bank - Mutual Funds", "is_manager": False, "login_customer_id": "1000000001", "parent": "1000000001"},
    {"id": "2000000002", "name": "Demo Retail", "is_manager": False, "login_customer_id": "1000000001", "parent": "1000000001"},
]

_PMAX = {
    "2000000001": [
        ("30000001", "MF_PMax_Always_On", "6600000001", "Flexicap_Fund_TopCities", "ENABLED"),
        ("30000001", "MF_PMax_Always_On", "6600000002", "ELSS_Fund_Tax_Season", "ENABLED"),
        ("30000002", "MF_PMax_SIP_Push", "6600000003", "SIP_Awareness_Metro", "PAUSED"),
    ],
    "2000000002": [("30000009", "Retail_PMax_Main", "6600000009", "Festive_Offers", "ENABLED")],
}

_SEARCH = {
    "2000000001": {
        ("40000001", "MS_Search_Flexicap_Brand_Exact"): [("50000001", "Brand Exact"), ("50000002", "Brand Phrase")],
        ("40000002", "MS_Search_SIP_Generic"): [("50000003", "SIP Generic")],
    },
    "2000000002": {("40000010", "Retail_Search_Brand"): [("50000010", "Brand")]},
}

_TERMS = ["sip", "mutual fund", "flexicap fund", "elss", "invest online", "nav", "returns", "demat",
          "fund house", "best sip plan", "tax saving fund", "lumpsum", "index fund", "amc"]


def _gen_keywords(seed: int) -> list[str]:
    rnd = random.Random(seed)
    out = []
    for a, b in itertools.product(_TERMS, ["demo", "online", "2026", "india", "app", "calculator"]):
        out.append(f"{b} {a}")
    rnd.shuffle(out)
    out = out[:70]
    # deliberate edge cases: duplicate (case/space), policy-banned, over-long
    out += [out[0].upper(), "  " + out[1] + "  ", "guaranteed returns mutual fund", "x" * 90]
    return out


class MockBackend:
    def __init__(self):
        self.created: list[dict] = []
        self._lock = Lock()
        self._seq = 6700000000

    def list_accounts(self):
        return [dict(a) for a in _ACCOUNTS]

    def list_pmax_asset_groups(self, customer_id, login_customer_id):
        return [
            {"customer_id": customer_id, "campaign_id": c, "campaign_name": cn,
             "asset_group_id": a, "asset_group_name": an, "status": st}
            for c, cn, a, an, st in _PMAX.get(customer_id, [])
        ]

    def list_search_structure(self, customer_id, login_customer_id):
        out = []
        for (cid, cname), ags in _SEARCH.get(customer_id, {}).items():
            out.append({"campaign_id": cid, "campaign_name": cname,
                        "ad_groups": [{"ad_group_id": i, "ad_group_name": n} for i, n in ags]})
        return out

    def list_keywords(self, customer_id, login_customer_id, campaign_ids, ad_group_ids, date_from, date_to, min_clicks):
        rows = []
        for (cid, cname), ags in _SEARCH.get(customer_id, {}).items():
            for agid, agname in ags:
                if campaign_ids and cid not in campaign_ids:
                    continue
                if ad_group_ids and agid not in ad_group_ids:
                    continue
                for kw in _gen_keywords(int(agid)):
                    clicks = (zlib.crc32((kw + agid).encode()) % 90) + 1
                    if clicks < min_clicks:
                        continue
                    rows.append({"campaign_id": cid, "campaign_name": cname, "ad_group_id": agid,
                                 "ad_group_name": agname, "keyword": kw, "clicks": clicks,
                                 "conversions": round((clicks % 7) * 0.8, 1), "cost": round(clicks * 1.3, 2)})
        return rows

    def describe_asset_group(self, customer_id, login_customer_id, asset_group_id):
        name = next((an for ags in _PMAX.values() for _, _, a, an, _ in ags if a == asset_group_id), None)
        if not name:
            raise AdsError("Asset group not found")
        types = ["HEADLINE"] * 5 + ["LONG_HEADLINE"] * 2 + ["DESCRIPTION"] * 3 + ["MARKETING_IMAGE"] * 2 + \
                ["SQUARE_MARKETING_IMAGE"] * 2 + ["LOGO", "BUSINESS_NAME"]
        return {
            "name": name,
            "final_urls": ["https://example.com/mutual-funds"],
            "final_mobile_urls": [],
            "path1": "invest", "path2": "sip",
            "assets": [{"field_type": t, "resource_name": f"customers/{customer_id}/assets/{9000 + i}"}
                       for i, t in enumerate(types)],
            "audiences": [f"customers/{customer_id}/audiences/111"],
        }

    def create_asset_group(self, customer_id, login_customer_id, campaign_id, source, name, themes,
                           copy_audiences, validate_only):
        banned = []
        for t in themes:
            for w, policy in _BANNED_WORDS.items():
                if w in t.lower():
                    banned.append({"theme": t, "policy": policy})
        if banned:
            return ChunkResult(ok=False, banned=banned)
        if validate_only:
            return ChunkResult(ok=True)
        with self._lock:
            self._seq += 3
            agid = str(self._seq)
            rn = f"customers/{customer_id}/assetGroups/{agid}"
            self.created.append({"resource_name": rn, "name": name, "campaign_id": campaign_id,
                                 "themes": list(themes), "status": "PAUSED"})
        return ChunkResult(ok=True, resource_name=rn, asset_group_id=agid)
