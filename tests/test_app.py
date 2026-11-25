import re

import pytest
from fastapi.testclient import TestClient

from app.auth.access import AccessPolicy, FileAccessStore
from app.config import Settings
from app.main import create_app


def make(tmp_path, **kw):
    s = Settings(_env_file=None, data_dir=str(tmp_path), **{"secret_key": "test", **kw})
    return create_app(s)


@pytest.fixture
def client(tmp_path):
    return TestClient(make(tmp_path))


def login(client):
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = client.post("/login", data={"username": "demo", "password": "demo123", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    html = client.get("/").text
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def h(tok):
    return {"X-CSRF-Token": tok}


# ---------------------------------------------------------------- auth
def test_unauthenticated_redirects_and_api_401(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/me").status_code == 401


def test_bad_password_rejected(client):
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    r = client.post("/login", data={"username": "demo", "password": "x", "csrf": csrf})
    assert r.status_code == 401


def test_login_without_csrf_rejected(client):
    assert client.post("/login", data={"username": "demo", "password": "demo123"}).status_code == 401


def test_post_requires_csrf_header(client):
    login(client)
    assert client.post("/api/plan", json={}).status_code == 403


def test_static_login_refused_on_cloud_run(tmp_path, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "pmax")
    with pytest.raises(RuntimeError):
        make(tmp_path)


def test_static_login_refused_in_prod_env(tmp_path):
    with pytest.raises(RuntimeError):
        make(tmp_path, env="prod")


def test_security_headers(client):
    r = client.get("/login")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY"


# ----------------------------------------------------------- access policy
def test_access_policy(tmp_path):
    p = AccessPolicy(FileAccessStore(tmp_path / "a.json"), ["boss@x.com"], ["seed@y.com"], ["corp.com"])
    assert p.is_allowed("boss@x.com") and p.resolve("boss@x.com").is_admin
    assert p.is_allowed("SEED@y.com") and not p.resolve("seed@y.com").is_admin
    assert p.is_allowed("anyone@corp.com")
    assert not p.is_allowed("evil@other.com")
    p.add("email", "client@other.com")
    assert p.is_allowed("client@other.com")
    p.add("domain", "@partner.org")
    assert p.is_allowed("a@partner.org")
    p.remove("email", "client@other.com")
    assert not p.is_allowed("client@other.com")
    with pytest.raises(ValueError):
        p.add("email", "not-an-email")


class HeaderAuth:
    def current_identity(self, request):
        e = request.headers.get("x-test-email")
        return (e, "") if e else None


def test_non_allowed_user_gets_403_and_admin_endpoints_need_admin(tmp_path):
    app = make(tmp_path, allowed_emails="user@ok.com")
    app.state.c.auth = HeaderAuth()
    c = TestClient(app)
    assert c.get("/api/me", headers={"x-test-email": "stranger@evil.com"}).status_code == 403
    assert c.get("/api/me", headers={"x-test-email": "user@ok.com"}).status_code == 200
    assert c.get("/api/admin/access", headers={"x-test-email": "user@ok.com"}).status_code == 403
    assert c.get("/api/admin/access", headers={"x-test-email": "demo@local"}).status_code == 200


def test_admin_can_grant_access(tmp_path):
    app = make(tmp_path)
    app.state.c.auth = HeaderAuth()
    c = TestClient(app)
    adm = {"x-test-email": "demo@local"}
    with c:
        tok = re.search(r'content="([^"]+)"', c.get("/", headers=adm).text.split('csrf-token')[1]).group(1)
        assert c.get("/api/me", headers={"x-test-email": "new@client.org"}).status_code == 403
        r = c.post("/api/admin/access/add", json={"kind": "domain", "value": "client.org"}, headers={**adm, **h(tok)})
        assert r.status_code == 200
        assert c.get("/api/me", headers={"x-test-email": "new@client.org"}).status_code == 200


# --------------------------------------------------------------- flow (mock)
def flow_payload(c):
    acc = c.get("/api/accounts").json()["accounts"]
    cust = next(a for a in acc if not a["is_manager"])
    pm = c.get(f"/api/accounts/{cust['id']}/pmax-asset-groups?login={cust['login_customer_id']}").json()["rows"][0]
    st = c.get(f"/api/accounts/{cust['id']}/search-structure?login={cust['login_customer_id']}").json()["campaigns"]
    return cust, pm, st


def test_full_mock_flow_dry_run_only_by_default(client):
    tok = login(client)
    cust, pm, st = flow_payload(client)
    kws = client.post("/api/keywords", headers=h(tok), json={
        "customer_id": cust["id"], "login_customer_id": cust["login_customer_id"],
        "ad_group_ids": [a["ad_group_id"] for a in st[0]["ad_groups"]]}).json()["rows"]
    assert len(kws) > 50
    body = {"customer_id": cust["id"], "login_customer_id": cust["login_customer_id"],
            "campaign_id": pm["campaign_id"], "asset_group_id": pm["asset_group_id"],
            "keywords": [r["keyword"] for r in kws]}
    v = client.post("/api/validate", headers=h(tok), json=body).json()
    assert v["source"]["asset_total"] > 0
    banned = [b for r in v["results"] for b in r["banned"]]
    assert any("guaranteed" in b["theme"] for b in banned)           # mock policy catch
    assert all(r["status"] == "validated" for r in v["results"])
    r = client.post("/api/run", headers=h(tok), json={**body, "confirm_groups": len(v["results"])})
    assert r.status_code == 403 and "disabled" in r.json()["detail"]
    assert client.app.state.c.backend.created == []


def test_run_when_enabled_requires_confirmation_and_creates_paused(tmp_path):
    c = TestClient(make(tmp_path, allow_mutate=True))
    tok = login(c)
    cust, pm, _ = flow_payload(c)
    body = {"customer_id": cust["id"], "login_customer_id": cust["login_customer_id"],
            "campaign_id": pm["campaign_id"], "asset_group_id": pm["asset_group_id"],
            "keywords": [f"kw {i}" for i in range(30)]}
    assert c.post("/api/run", headers=h(tok), json={**body, "confirm_groups": 1}).status_code == 400
    ok = c.post("/api/run", headers=h(tok), json={**body, "confirm_groups": 2})
    assert ok.status_code == 200
    created = c.app.state.c.backend.created
    assert len(created) == 2 and all(x["status"] == "PAUSED" for x in created)
    audit = c.get("/api/admin/audit").json()["rows"]
    assert any(a["action"] == "run_done" for a in audit)


def test_input_validation(client):
    tok = login(client)
    r = client.post("/api/plan", headers=h(tok), json={"customer_id": "abc", "campaign_id": "1", "asset_group_id": "1", "keywords": ["a"]})
    assert r.status_code == 422


# ------------------------------------------------------- basic auth (Cloud Run)
BASIC = dict(auth_mode="basic", env="prod", app_user="ops", app_password="a-long-password-1",
             admin_emails="admin@corp.com", secret_key="x" * 32)


def test_basic_mode_allowed_on_cloud_run_with_real_password(tmp_path, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "pmax")
    c = TestClient(make(tmp_path, **BASIC), base_url="https://testserver")  # Secure cookie
    page = c.get("/login")
    assert "demo123" not in page.text and "ops" not in page.text      # no credential hint
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    assert c.post("/login", data={"username": "ops", "password": "bad", "csrf": csrf}).status_code == 401
    r = c.post("/login", data={"username": "ops", "password": "a-long-password-1", "csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert c.get("/api/me").json()["user"]["email"] == "admin@corp.com"


@pytest.mark.parametrize("bad", [dict(app_password="demo123"), dict(app_password="short"),
                                 dict(secret_key=""), dict(admin_emails="demo@local")])
def test_basic_mode_refuses_weak_config(tmp_path, bad):
    with pytest.raises(RuntimeError):
        make(tmp_path, **{**BASIC, **bad})


def test_real_backend_in_prod_needs_fixed_creds(tmp_path):
    with pytest.raises(RuntimeError):
        make(tmp_path, **BASIC, ads_backend="real")


def test_env_credentials_used_when_complete():
    from app.ads.credentials import EnvCredentials
    s = Settings(_env_file=None, google_ads_developer_token="d", google_ads_client_id="i",
                 google_ads_client_secret="s", google_ads_refresh_token="r", google_ads_login_customer_id="123-456-7890")
    assert s.env_creds_complete
    cfg = EnvCredentials(s).config()
    assert cfg["refresh_token"] == "r" and cfg["login_customer_id"] == "1234567890"


# ------------------------------------------- per-user Google Ads consent (ADS_ACCESS=user)
USER_MODE = dict(ads_access="user", google_ads_client_id="cid", google_ads_client_secret="sec",
                 admin_emails="boss@corp.com", allowed_domains="corp.com")


def user_client(tmp_path, grant):
    app = make(tmp_path, **USER_MODE)
    app.state.c.oauth.exchange = lambda code, redirect, verifier: grant      # no real Google call
    return TestClient(app)


def start_consent(c):
    r = c.get("/oauth/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("https://accounts.google.com/")
    q = dict(p.split("=", 1) for p in r.headers["location"].split("?", 1)[1].split("&"))
    assert "adwords" in q["scope"] and q["access_type"] == "offline" and q["code_challenge_method"] == "S256"
    return q["state"]


def test_user_mode_login_then_consent_flow(tmp_path):
    c = user_client(tmp_path, {"email": "ann@corp.com", "name": "Ann", "refresh_token": "RT-secret"})
    page = c.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    c.post("/login", data={"username": "demo", "password": "demo123", "csrf": csrf})
    # logged in with the shared login, but not yet connected
    assert c.get("/api/me").status_code == 409
    assert c.get("/", follow_redirects=False).headers["location"] == "/connect"
    assert "Connect with Google" in c.get("/connect").text
    state = start_consent(c)
    assert c.get("/oauth/callback?code=x&state=WRONG", follow_redirects=False).headers["location"].startswith("/connect?error")
    state = start_consent(c)
    r = c.get(f"/oauth/callback?code=x&state={state}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = c.get("/api/me").json()
    assert me["user"]["email"] == "ann@corp.com" and me["credentials"]["mode"] == "user-oauth"
    assert "RT-secret" not in c.cookies.get("pmax_session", "")          # token only inside the signed cookie, encrypted
    assert c.get("/api/accounts").status_code == 200
    # disconnect -> must connect again
    tok = re.search(r'name="csrf-token" content="([^"]+)"', c.get("/").text).group(1)
    assert c.post("/api/ads/disconnect", headers=h(tok)).status_code == 200
    assert c.get("/api/me").status_code == 409


def test_user_mode_blocks_google_accounts_outside_allow_list(tmp_path):
    c = user_client(tmp_path, {"email": "eve@evil.com", "name": "", "refresh_token": "RT"})
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)
    c.post("/login", data={"username": "demo", "password": "demo123", "csrf": csrf})
    state = start_consent(c)
    r = c.get(f"/oauth/callback?code=x&state={state}", follow_redirects=False)
    assert r.status_code == 403
    assert c.get("/api/me").status_code == 409                           # still not connected


def test_oauth_routes_need_basic_login_first(tmp_path):
    c = user_client(tmp_path, {})
    assert c.get("/oauth/start", follow_redirects=False).headers["location"] == "/login"
    assert c.get("/connect", follow_redirects=False).headers["location"] == "/login"


def test_user_mode_needs_oauth_client_settings(tmp_path):
    with pytest.raises(RuntimeError):
        make(tmp_path, ads_access="user")


def test_user_backends_require_token_and_reject_tampering():
    from app.ads.base import AdsReauthError
    from app.auth.google_oauth import TokenCrypto, UserBackends
    s = Settings(_env_file=None)
    crypto = TokenCrypto("k1")
    ub = UserBackends(s, crypto)
    with pytest.raises(AdsReauthError):
        ub.for_session({}, "a@b.com")
    with pytest.raises(AdsReauthError):
        ub.for_session({"ads_rt": TokenCrypto("other-key").encrypt("rt")}, "a@b.com")
    a = ub.for_session({"ads_rt": crypto.encrypt("rt-A")}, "a@b.com")
    b = ub.for_session({"ads_rt": crypto.encrypt("rt-B")}, "b@b.com")
    assert a is not b and a is ub.for_session({"ads_rt": crypto.encrypt("rt-A")}, "a@b.com")


def test_oauth_client_json_secret_is_parsed():
    import json
    s = Settings(_env_file=None, google_oauth_client_json=json.dumps({"web": {"client_id": "cid", "client_secret": "sec"}}))
    assert (s.google_ads_client_id, s.google_ads_client_secret) == ("cid", "sec")
    with pytest.raises(Exception):
        Settings(_env_file=None, google_oauth_client_json="not json")


def test_ads_credentials_single_json_secret():
    import json
    blob = json.dumps({"developer_token": "d", "client_id": "i", "client_secret": "s", "refresh_token": "r",
                       "login_customer_id": "123"})
    s = Settings(_env_file=None, google_ads_credentials_json=blob)
    assert s.env_creds_complete and s.google_ads_login_customer_id == "123"
    with pytest.raises(Exception):
        Settings(_env_file=None, google_ads_credentials_json="{bad")


# ---------------------------------------------------- multi-user login (APP_USERS_JSON)
import json as _json

MULTI = dict(BASIC, app_users_json=_json.dumps({"admin@corp.com": "admin-password-12", "user@corp.com": "user-password-123"}))


def _login_as(c, user, pw):
    csrf = re.search(r'name="csrf" value="([^"]+)"', c.get("/login").text).group(1)
    return c.post("/login", data={"username": user, "password": pw, "csrf": csrf}, follow_redirects=False)


def test_multi_user_admin_and_normal_user(tmp_path):
    adm = TestClient(make(tmp_path, **MULTI), base_url="https://testserver")
    assert _login_as(adm, "ADMIN@corp.com", "admin-password-12").status_code == 303        # username is case-insensitive
    me = adm.get("/api/me").json()["user"]
    assert me["email"] == "admin@corp.com" and me["is_admin"] is True
    usr = TestClient(make(tmp_path, **MULTI), base_url="https://testserver")
    assert _login_as(usr, "user@corp.com", "user-password-123").status_code == 303
    me = usr.get("/api/me").json()["user"]
    assert me["email"] == "user@corp.com" and me["is_admin"] is False
    assert usr.get("/api/admin/access").status_code == 403                                 # not an admin
    assert usr.get("/admin", follow_redirects=False).status_code == 403


def test_multi_user_wrong_password_or_name(tmp_path):
    c = TestClient(make(tmp_path, **MULTI), base_url="https://testserver")
    assert _login_as(c, "user@corp.com", "admin-password-12").status_code == 401           # another user's password
    assert _login_as(c, "ops", "admin-password-12").status_code == 401                     # old shared username gone


def test_multi_user_config_validation(tmp_path):
    with pytest.raises(RuntimeError):
        make(tmp_path, **{**BASIC, "app_users_json": _json.dumps({"not-an-email": "long-enough-password"})})
    with pytest.raises(RuntimeError):
        make(tmp_path, **{**BASIC, "app_users_json": _json.dumps({"a@b.com": "short"})})
    with pytest.raises(RuntimeError):
        make(tmp_path, **{**BASIC, "app_users_json": "[1,2]"})
