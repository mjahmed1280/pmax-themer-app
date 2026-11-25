"""JSON API used by the wizard."""
from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.ads.base import AdsBackend
from app.ads.themer import MAX_BUCKET, build_plan, process_chunks
from app.auth.base import User
from app.deps import admin_user, api_user, container, user_ads

router = APIRouter(prefix="/api")


# ----------------------------------------------------------------- models
class AccountRef(BaseModel):
    customer_id: str = Field(pattern=r"^\d{6,12}$")
    login_customer_id: str | None = Field(default=None, pattern=r"^\d{6,12}$")


class KeywordQuery(AccountRef):
    campaign_ids: list[str] = []
    ad_group_ids: list[str] = []
    date_from: str | None = None
    date_to: str | None = None
    min_clicks: int = Field(default=1, ge=0, le=1_000_000)


class PlanRequest(AccountRef):
    campaign_id: str = Field(pattern=r"^\d+$")          # target PMax campaign
    asset_group_id: str = Field(pattern=r"^\d+$")       # source/clone asset group
    keywords: list[str] = Field(max_length=5000)
    bucket_size: int = Field(default=MAX_BUCKET, ge=1, le=MAX_BUCKET)
    name_prefix: str = Field(default="PMax Themer", max_length=100)
    copy_audiences: bool = True


class RunRequest(PlanRequest):
    confirm_groups: int


class AccessChange(BaseModel):
    kind: str
    value: str = Field(max_length=200)


# ------------------------------------------------------------------ basics
@router.get("/me")
def me(request: Request, user: User = Depends(api_user)):
    c = container(request)
    s = c.settings
    creds = c.creds_status
    if s.ads_access == "user":
        creds = {"mode": "user-oauth", "connected": True,
                 "detail": f"Using {user.email}'s own Google Ads access"}
    return {"user": user.to_dict(), "org": s.org_name, "env": s.env, "ads_backend": s.ads_backend,
            "allow_mutate": s.allow_mutate, "credentials": creds, "max_bucket": MAX_BUCKET}


@router.get("/accounts")
def accounts(ads: AdsBackend = Depends(user_ads)):
    return {"accounts": ads.list_accounts()}


@router.get("/accounts/{customer_id}/pmax-asset-groups")
def pmax_asset_groups(customer_id: str, login: str | None = None, ads: AdsBackend = Depends(user_ads)):
    return {"rows": ads.list_pmax_asset_groups(customer_id, login)}


@router.get("/accounts/{customer_id}/search-structure")
def search_structure(customer_id: str, login: str | None = None, ads: AdsBackend = Depends(user_ads)):
    return {"campaigns": ads.list_search_structure(customer_id, login)}


@router.post("/keywords")
def keywords(q: KeywordQuery, ads: AdsBackend = Depends(user_ads)):
    today = date.today()
    df = q.date_from or (today - timedelta(days=90)).isoformat()
    dt = q.date_to or today.isoformat()
    if not q.campaign_ids and not q.ad_group_ids:
        raise HTTPException(400, "Select at least one search campaign or ad group")
    rows = ads.list_keywords(
        q.customer_id, q.login_customer_id, q.campaign_ids, q.ad_group_ids, df, dt, q.min_clicks)
    return {"rows": rows, "date_from": df, "date_to": dt}


# ------------------------------------------------------------- plan / run
def _source_summary(src: dict) -> dict:
    return {
        "name": src["name"],
        "final_url": (src.get("final_urls") or [None])[0],
        "asset_counts": dict(sorted(Counter(a["field_type"] for a in src["assets"]).items())),
        "asset_total": len(src["assets"]),
        "audiences": len(src.get("audiences", [])),
    }


def _prepare(req: PlanRequest, request: Request, ads: AdsBackend):
    c = container(request)
    if not req.keywords:
        raise HTTPException(400, "No keywords selected")
    plan = build_plan(req.keywords, req.bucket_size, req.name_prefix)
    if not plan.chunks:
        raise HTTPException(400, "No usable keywords after cleaning")
    source = ads.describe_asset_group(req.customer_id, req.login_customer_id, req.asset_group_id)
    target = {"customer_id": req.customer_id, "login_customer_id": req.login_customer_id,
              "campaign_id": req.campaign_id}
    return c, plan, source, target


@router.post("/plan")
def make_plan(req: PlanRequest, request: Request, ads: AdsBackend = Depends(user_ads)):
    _c, plan, source, _t = _prepare(req, request, ads)
    return {"plan": plan.to_dict(), "source": _source_summary(source)}


@router.post("/validate")
def validate(req: PlanRequest, request: Request, user: User = Depends(api_user),
             ads: AdsBackend = Depends(user_ads)):
    """Dry-run: validate_only mutate per chunk (no writes) to pre-check policy."""
    c, plan, source, target = _prepare(req, request, ads)
    results = process_chunks(ads, target, source, plan, req.copy_audiences, validate_only=True)
    c.audit.write(user.email, "validate", customer_id=req.customer_id, campaign_id=req.campaign_id,
                  asset_group_id=req.asset_group_id, groups=len(plan.chunks),
                  banned=sum(len(r["banned"]) for r in results))
    return {"plan": plan.to_dict(), "source": _source_summary(source), "results": results}


@router.post("/run")
def run(req: RunRequest, request: Request, user: User = Depends(api_user),
        ads: AdsBackend = Depends(user_ads)):
    c, plan, source, target = _prepare(req, request, ads)
    if not c.settings.allow_mutate:
        raise HTTPException(403, "Writes are disabled on this deployment (ALLOW_MUTATE=false). "
                                 "Use the dry-run, or ask the admin to enable writes.")
    if req.confirm_groups != len(plan.chunks):
        raise HTTPException(400, f"Confirmation mismatch: this run creates {len(plan.chunks)} asset groups")
    c.audit.write(user.email, "run_start", customer_id=req.customer_id, campaign_id=req.campaign_id,
                  asset_group_id=req.asset_group_id, groups=len(plan.chunks), themes=plan.stats["unique"])
    results = process_chunks(ads, target, source, plan, req.copy_audiences, validate_only=False)
    c.audit.write(user.email, "run_done", customer_id=req.customer_id, campaign_id=req.campaign_id,
                  created=[r["resource_name"] for r in results if r["status"] == "created"],
                  failed=[{"name": r["name"], "error": r["error"]} for r in results if r["status"] in ("failed", "skipped")])
    return {"plan": plan.to_dict(), "results": results,
            "ads_link": f"https://ads.google.com/aw/assetgroups?campaignId={req.campaign_id}&__e={req.customer_id}"}


@router.post("/ads/disconnect")
def disconnect(request: Request, user: User = Depends(api_user)):
    """Forget this user's Google Ads access (revoke at Google, drop from the session)."""
    c = container(request)
    blob = request.session.pop("ads_rt", None)
    if blob and c.oauth and c.crypto:
        try:
            c.oauth.revoke(c.crypto.decrypt(blob))
        except Exception:
            pass
    request.session.pop("email", None)
    request.session.pop("name", None)
    c.audit.write(user.email, "ads_disconnect")
    return {"ok": True}


# ------------------------------------------------------------------ admin
@router.get("/admin/access")
def admin_access(request: Request, user: User = Depends(admin_user)):
    return container(request).access.snapshot()


@router.post("/admin/access/add")
def admin_add(ch: AccessChange, request: Request, user: User = Depends(admin_user)):
    c = container(request)
    try:
        c.access.add(ch.kind, ch.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    c.audit.write(user.email, "access_add", kind=ch.kind, value=ch.value.strip().lower())
    return c.access.snapshot()


@router.post("/admin/access/remove")
def admin_remove(ch: AccessChange, request: Request, user: User = Depends(admin_user)):
    c = container(request)
    try:
        c.access.remove(ch.kind, ch.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    c.audit.write(user.email, "access_remove", kind=ch.kind, value=ch.value.strip().lower())
    return c.access.snapshot()


@router.get("/admin/audit")
def admin_audit(request: Request, user: User = Depends(admin_user)):
    return {"rows": container(request).audit.tail(100)}
