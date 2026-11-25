# PMax Themer Web App: Build Plan

Goal: replace the Colab notebook (`ICICI_pmax_themer.ipynb`) with a web UI that non-technical users can run. The first target is local testing with a static login. The second is Cloud Run behind IAP, with each user's own Google Ads permissions.

## Status and decisions (updated after approval)

**Built and tested locally (P0–P4):** wizard UI, static login, admin access control, mock and real read-only backends, plan builder, dry-run (`validate_only`), guarded executor, audit log, 23 unit/API tests, and a browser smoke test. No asset group or search theme has been created in any real account. `ALLOW_MUTATE` is `false`.

**Decisions from the owner:**
1. Stack: FastAPI. The front end is plain JS + a JSON API instead of HTMX, which means fewer dependencies and a strict CSP with no inline scripts.
2. Prod auth: option A (IAP + per-user Google Ads OAuth). The app will be deployed into different clients' GCP projects, and users may belong to other organisations.
   - Each deployment has its own tech admin, who controls access through `ADMIN_EMAILS`, `ALLOWED_EMAILS` and `ALLOWED_DOMAINS` and the `/admin` page. This is built.
   - IAP's allowed principals should be set to match, as a first gate. The app's own policy is the second.
   - Each person grants Google Ads access themselves ("Connect Google Ads" consent), so they only see accounts they already have permission for. The consent screen type (Internal or External with verification) is a per-client decision for the GCP phase.
3. Case 1 only. The notebook has no Case 2 implementation, so it is out of scope for v1. A per-ad-group variant can be added later as a new strategy option.
4. Runs are synchronous.
5. Local dev reads the existing `google-ads.yaml`, only for the real backend and read-only calls.

**Still to do (GCP phase, separate plan):** `app/auth/iap.py` (JWT verification), `app/auth/oauth_ads.py` (per-user OAuth + encrypted token store), Firestore-backed access store, Dockerfile, Cloud Run, Secret Manager, and a per-client deployment checklist.

**Unverified against the real API:** the atomic create (asset group + assets + audiences + themes in one `validate_only` or real mutate). The read paths were checked against the real account and match the notebook. The write path is only tested against the mock.

The rest of this document is the original plan, kept for reference.

---

## 1. What the notebook does (the flow to turn into a UI)

| Notebook step | What it does | UI step |
|---|---|---|
| Expand MCC | Lists the customer IDs under an MCC | **1. Account** |
| Target PMax query | Lists enabled PMax asset groups (hardcoded to `Flexicap_Fund_TopCities`) | **2. Target** |
| `getKeywords` | Reads enabled Standard Search keywords with clicks > 0 from `keyword_view` | **3. Source** |
| Campaign/ad group selection | Filters keywords by `;`-separated IDs | **3. Source** (checkbox tables) |
| Case 1 (`create_assetgroups_with_signals`) | Dedupes keywords, splits them into chunks of 25, clones the source asset group as a PAUSED asset group per chunk, copies audiences, and adds search themes. Policy-banned themes are removed and the chunk retried. | **4. Strategy**, **5. Review**, **6. Run** |
| Case 2 (preserve ad group structure) | Only the imports exist in the notebook, so it is unimplemented. | Phase 2 (optional) |

## 2. Notebook problems to fix during the port

1. **Hardcoded secrets in the notebook.** The developer token, OAuth client secret and a refresh token are in plain text in `ICICI_pmax_themer.ipynb`, which sits in a OneDrive-synced folder.
   - The new app will not copy them anywhere.
   - I recommend rotating the client secret and refresh token, and checking whether the developer token needs rotating.
2. **Possible infinite loop.** In `create_assetgroups_with_signals`, the "unknown error" branch prints the error but never breaks. If the failure isn't a banned theme, the same chunk is retried forever.
   - Fix: bounded retries, then fail that chunk with a clear message.
3. **Hardcoded filters.** The asset group name (`Flexicap_Fund_TopCities`) and the IDs are hardcoded. They become UI selectors.
4. **No date range on the keyword query.** `metrics.clicks > 0` currently means all time. The UI gets a date-range picker and a min-clicks input.
5. **Dedupe loses order.** `list(set(...))` is replaced with an order-preserving dedupe, case-insensitive and trimmed.
6. **Asset group naming.** Names are `test-AdxAi-PMaxThemer ... #uuid`. The UI makes the prefix configurable and uses a readable suffix, for example `<prefix> – <source campaign> – batch 1/4`.
7. **URL handling.** The `[1:]` slicing on URLs is brittle, so the port reads the URL fields directly.
8. **No result log.** The UI shows a results table and a CSV download, and writes an audit log row of who did what.

## 3. Tech stack

**Recommended: FastAPI + Jinja2 templates + HTMX (+ a little Alpine.js), in a single container.**

- It is Python, so the Ads logic ports over directly.
- It is one small Cloud Run service, with no separate frontend build and no websocket requirement.
- It has first-class middleware for the IAP JWT and OAuth later.
- It can render a proper wizard UI. Streamlit, the main alternative, is quicker to build but hard to fit into IAP, per-user OAuth and real layout control.
- Ads access uses the official `google-ads` client directly, with the notebook's GAQL queries copied over. This drops the `gaarf` and pandas dependency, which is heavy and unnecessary here.

## 4. UI layout

Single-page wizard with a left stepper and a sticky "Selection summary" panel on the right.

```
┌──────────────────────────────────────────────────────────────────────┐
│ PMax Themer            [env badge: LOCAL / DRY-RUN]   user ▾ Sign out │
├──────────────┬───────────────────────────────────┬───────────────────┤
│ ① Account    │  Step 3 · Source Search keywords  │ Selection summary │
│ ② Target PMax│                                   │ Account: ICICI    │
│ ③ Source  ◄──│  Campaign(s) [multi-select ▾]     │   (7904387682)    │
│ ④ Strategy   │  Ad group(s) [optional filter ▾]  │ Target AG:        │
│ ⑤ Review     │  Date range [last 90d ▾] Min      │   Flexicap_...    │
│ ⑥ Run        │  clicks [1]                       │ Source: 1 camp    │
│              │  ┌───────────────────────────┐    │ Keywords: 81      │
│              │  │☑ keyword   clicks  conv  │    │ After dedupe: 79  │
│              │  │☑ icici sip   120    4    │    │ Asset groups: 4   │
│              │  └───────────────────────────┘    │ (25/25/25/4)      │
│              │  [Select all] [Clear] [Paste]     │                   │
│              │            [Back]  [Next →]       │                   │
└──────────────┴───────────────────────────────────┴───────────────────┘
```

Steps:

1. **Account:** a dropdown of customer accounts the signed-in user can access, grouped by MCC. Selecting an MCC sets `login_customer_id` and loads its children.
2. **Target PMax:** a searchable table of campaign → asset group (status, cost) with a radio selection. Replaces cell 8 and cell 10.
3. **Source:** campaign and ad group multi-selects, a date range and min clicks. It shows an editable keyword table with checkboxes, plus a "paste extra themes" box. Counts update live.
4. **Strategy:**
   - Case 1, equal buckets, with a bucket size of at most 25.
   - Case 2, one asset group per search ad group, if built.
   - Name prefix.
   - "Copy customer-scoped audiences" toggle.
   - New asset group status, which is locked to PAUSED.
5. **Review (dry-run):**
   - The exact plan: N asset groups, how many themes in each, which assets and URLs are cloned, which audiences are reused.
   - A policy pre-check using the API's `validate_only` mutate. Banned themes are listed and can be unchecked here, with no write.
   - Warnings, such as a theme count that is too high or an empty URL.
6. **Run:** a confirm dialog (type the asset group count to confirm). Progress runs per chunk, then a results table with deep links to each new asset group in the Google Ads UI, a CSV download and an audit entry.

Visual style is a clean neutral design with one accent colour. It has an accessible contrast ratio, a responsive layout, and loading, empty and error states on every step.

## 5. Authentication design

### 5a. Local test (what we build first)

- The login page is static: `/login` with a username/password form.
- Defaults are `demo` / `demo123`, overridable via `.env` (`APP_USER`, `APP_PASSWORD`). They are shown on the page only when `ENV=local`.
- Sessions are a signed, HTTP-only cookie (Starlette `SessionMiddleware`), with CSRF tokens on all POSTs.
- **Guard:** the app refuses to start with `AUTH_MODE=static` if it detects Cloud Run (`K_SERVICE` is set). The static login can never ship by accident.
- Google Ads credentials locally come from the existing `google-ads.yaml` (gitignored, never copied into the new folder), pointed to via `GOOGLE_ADS_YAML_PATH`.
- A **mock Ads backend** (`ADS_BACKEND=mock`) serves fake accounts, campaigns and keywords. This lets the whole UI be built and tested with zero API calls. The real backend is `ADS_BACKEND=real`.

### 5b. Production: using each user's own Google Ads credentials

IAP and the Ads API are separate problems:

- **IAP proves who the user is.** It gives the app a verified email through the `x-goog-iap-jwt-assertion` header.
- **IAP does not hand the app a Google Ads access token.** The user's Ads permissions have to come from a separate OAuth grant.

| Option | How | Pros | Cons |
|---|---|---|---|
| **A (recommended). IAP + per-user OAuth** | IAP gates the app. On first use the user clicks "Connect Google Ads" and consents to the `adwords` scope. The app stores their refresh token encrypted (KMS-wrapped, in Firestore). Every Ads call runs as that user, with the app's developer token. | Each user sees and changes only what their own Google Ads access allows. Google Ads logs show the real user. No shared super-credential. | Needs a one-time consent per user. Needs a token store. Revocation and expiry handling is required. |
| B. Domain-wide delegation | A service account impersonates the IAP user (the `google-ads` client supports `impersonated_email`). | No per-user consent. | Only works for users in our Workspace domain. Needs a Workspace admin to grant a broad DWD scope. A compromised service account can impersonate anyone. I want to re-check the current docs before recommending it. |
| C. One shared credential (today's notebook) | Everyone runs as one refresh token. | Simplest. | No per-user permissions or audit. Everyone gets the access of the most-privileged user. Not recommended. |

Details for option A:

- The OAuth consent screen: if every user is in the insidemedia.net Workspace, set it to **Internal** and no Google verification is needed. If client staff outside the domain will use it, the `adwords` scope is sensitive and the app needs Google verification.
- The OAuth identity email must match the IAP email, or the connect is rejected.
- The developer token and OAuth client secret live in Secret Manager, never in the repo or the image.
- Cloud Run service identity: its own service account, with least privilege (Secret Manager accessor, KMS encrypt/decrypt, Firestore).
- `login_customer_id` is set from the MCC the user picks in step 1.
- IAP's JWT is verified on every request (audience and signature check). The app does not trust the plain email header.
- The auth layer is behind an interface (`AuthProvider` + `CredentialProvider`), so local static login and prod IAP/OAuth swap by config with no changes to the Ads or UI code.

The GCP phase gets its own plan after the local build is approved and working.

## 6. Safety rules (the app must enforce these)

- **`ALLOW_MUTATE=false` by default.** With it false, "Run" is disabled and only the dry-run preview and `validate_only` check are available. Real writes need the flag set explicitly.
- During this build and its testing, `ALLOW_MUTATE` stays false. Nothing creates a real asset group or search theme without your say-so.
- New asset groups are always created PAUSED.
- A confirm step is required before any write, and the audit log records user, account, source, target and result.
- Secrets are never logged, templated into pages, or committed. `.env`, `google-ads.yaml` and `*.json` key files go in `.gitignore`.
- Every Ads call goes through a single service module, so the mutate path is easy to review.

## 7. Folder structure (new, self-contained: `pmax-themer-app/`)

```
pmax-themer-app/
├── PLAN.md
├── README.md                 # run locally, env vars, safety flags
├── pyproject.toml / requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile                # added in the GCP phase, stubbed now
├── app/
│   ├── main.py               # FastAPI app, middleware, router wiring
│   ├── config.py             # pydantic-settings (ENV, AUTH_MODE, ADS_BACKEND, ALLOW_MUTATE)
│   ├── auth/
│   │   ├── base.py           # AuthProvider / CredentialProvider interfaces
│   │   ├── static_login.py   # local only
│   │   ├── iap.py            # prod stub: JWT verification
│   │   └── oauth_ads.py      # prod stub: per-user adwords OAuth
│   ├── ads/
│   │   ├── client.py         # builds GoogleAdsClient from creds provider
│   │   ├── queries.py        # GAQL from the notebook (accounts, PMax AGs, keywords)
│   │   ├── themer.py         # plan builder + executor (ported Case 1, bounded retries)
│   │   └── mock.py           # fake backend for UI work
│   ├── routers/              # pages.py, api_accounts.py, api_plan.py, api_run.py
│   ├── templates/            # base.html, login.html, wizard/*.html (HTMX partials)
│   └── static/               # css, small js
└── tests/                    # plan-builder (chunking, dedupe, banned-theme retry), auth guard, route smoke tests
```

## 8. Build phases (after you approve)

| Phase | Deliverable | Ads calls |
|---|---|---|
| **P0** | Scaffold, config, static login, wizard shell and layout, mock backend. Whole flow clickable with fake data. | None |
| **P1** | Real read-only queries (accounts, PMax asset groups, keywords) via the local `google-ads.yaml`. | Read only |
| **P2** | Plan builder (dedupe, chunking, naming, cloned-asset preview) and the Review step with `validate_only` policy pre-check. | Read plus `validate_only` |
| **P3** | Executor with the confirm dialog, progress, results, CSV and audit log, behind `ALLOW_MUTATE`. Built and unit-tested against the mock only. | None, until you enable it |
| **P4** | Tests, README and an error/empty-state pass. | None |
| **P5 (optional)** | Case 2, preserving ad group structure. | None |
| **Later** | GCP plan: Dockerfile, Cloud Run, IAP, Secret Manager, KMS and Firestore for tokens, OAuth consent screen. | |

I'd review with you at the end of P0 (the UI) before moving to real data.

## 9. Decisions I need from you

1. **Stack:** FastAPI + HTMX (recommended), or something else, such as React, or Streamlit for speed?
2. **Prod auth:** option A (IAP + per-user OAuth, recommended), B or C?
3. **Who are the users:** all insidemedia.net Workspace, or will outside client staff use it too? This decides the OAuth consent screen type.
4. **Case 2** (preserve ad group structure): in scope for the first version, or later?
5. **Cloud Run runs:** are synchronous runs fine for now? A run is a few API calls per chunk, so it should finish in seconds to a minute. Longer jobs would need Cloud Tasks and persistent job state in the GCP phase.
6. **Local dev credentials:** OK to read the existing `google-ads.yaml` for the read-only P1 queries?
