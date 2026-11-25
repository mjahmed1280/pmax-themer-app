"""Plan builder + executor (port of notebook 'Case 1').

Improvements over the notebook:
  * order-preserving, trim/case-insensitive dedupe
  * themes longer than the API limit are reported, not sent
  * each asset group is created in ONE atomic mutate (group + assets + audiences +
    themes) so a policy rejection never leaves an orphan asset group behind
  * bounded retries when themes are rejected (the notebook could loop forever)
  * validate_only dry-run using the same code path
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.ads.base import AdsBackend, AdsError

MAX_BUCKET = 25            # API limit: search themes per asset group
MAX_THEME_LEN = 80         # API limit: characters per search theme
MAX_NAME_LEN = 128
MAX_ATTEMPTS = 5


def clean_keyword(k: str) -> str:
    return re.sub(r"\s+", " ", (k or "").strip())


@dataclass
class Chunk:
    index: int
    name: str
    themes: list[str]


@dataclass
class Plan:
    chunks: list[Chunk]
    stats: dict
    skipped: list[dict] = field(default_factory=list)   # [{"theme":..., "reason":...}]

    def to_dict(self) -> dict:
        return {
            "chunks": [{"index": c.index, "name": c.name, "themes": c.themes} for c in self.chunks],
            "stats": self.stats,
            "skipped": self.skipped,
        }


def build_plan(keywords: list[str], bucket_size: int = MAX_BUCKET, name_prefix: str = "PMax Themer") -> Plan:
    bucket_size = max(1, min(int(bucket_size or MAX_BUCKET), MAX_BUCKET))
    seen: set[str] = set()
    unique: list[str] = []
    skipped: list[dict] = []
    dups = 0
    for raw in keywords:
        k = clean_keyword(raw)
        if not k:
            continue
        key = k.lower()
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        if len(k) > MAX_THEME_LEN:
            skipped.append({"theme": k, "reason": f"longer than {MAX_THEME_LEN} characters"})
            continue
        unique.append(k)

    n = (len(unique) + bucket_size - 1) // bucket_size
    prefix = clean_keyword(name_prefix) or "PMax Themer"
    chunks = []
    for i in range(n):
        name = f"{prefix} {i + 1}/{n}"[:MAX_NAME_LEN]
        chunks.append(Chunk(i, name, unique[i * bucket_size:(i + 1) * bucket_size]))
    return Plan(
        chunks=chunks,
        stats={
            "input": len(keywords),
            "duplicates_removed": dups,
            "too_long": len(skipped),
            "unique": len(unique),
            "bucket_size": bucket_size,
            "asset_groups": n,
            "sizes": [len(c.themes) for c in chunks],
        },
        skipped=skipped,
    )


def process_chunks(
    backend: AdsBackend,
    target: dict,
    source: dict,
    plan: Plan,
    copy_audiences: bool,
    validate_only: bool,
) -> list[dict]:
    """Run (or dry-run) every chunk. Returns one result dict per chunk. Never raises for a
    single chunk's failure: the error is recorded and the remaining chunks continue."""
    results = []
    for chunk in plan.chunks:
        themes = list(chunk.themes)
        banned_all: list[dict] = []
        res = {"index": chunk.index, "name": chunk.name, "status": "pending",
               "resource_name": None, "asset_group_id": None,
               "themes_requested": len(chunk.themes), "themes_final": 0,
               "banned": banned_all, "error": None, "themes": themes}
        try:
            for _attempt in range(MAX_ATTEMPTS):
                if not themes:
                    res["status"] = "skipped"
                    res["error"] = "All themes were rejected by policy"
                    break
                r = backend.create_asset_group(
                    customer_id=target["customer_id"],
                    login_customer_id=target.get("login_customer_id"),
                    campaign_id=target["campaign_id"],
                    source=source,
                    name=chunk.name,
                    themes=themes,
                    copy_audiences=copy_audiences,
                    validate_only=validate_only,
                )
                if r.ok:
                    res.update(status="validated" if validate_only else "created",
                               resource_name=r.resource_name, asset_group_id=r.asset_group_id,
                               themes_final=len(themes))
                    break
                bad = {b["theme"].lower() for b in r.banned}
                if not bad:
                    raise AdsError("Rejected without a usable policy detail")
                banned_all.extend(r.banned)
                themes = [t for t in themes if t.lower() not in bad]
                res["themes"] = themes
            else:
                raise AdsError(f"Still failing after {MAX_ATTEMPTS} attempts")
        except AdsError as e:
            res.update(status="failed", error=str(e))
        res["themes"] = themes
        results.append(res)
    return results
