# PMax Themer web app

Turns Search keywords into PMax search themes. It clones a PMax asset group into new **paused** asset groups, up to 25 themes each. This is the web version of the Colab notebook, built for non-technical users.

See [PLAN.md](PLAN.md) for the design and decisions.

## Run locally

```bash
python -m venv ~/.venvs/pmax-themer          # keep the venv outside OneDrive
~/.venvs/pmax-themer/Scripts/python -m pip install -r requirements-dev.txt
cp .env.example .env                          # optional; defaults work
~/.venvs/pmax-themer/Scripts/python -m uvicorn app.main:app --port 8080
```

Open http://localhost:8080 and sign in with `demo` / `demo123` (override with `APP_USER` / `APP_PASSWORD`).

| Setting | Values | Meaning |
|---|---|---|
| `ADS_BACKEND` | `mock` (default), `real` | `mock` serves fake accounts and keywords with no API calls. `real` uses the Google Ads API with `GOOGLE_ADS_YAML_PATH` (local dev only). |
| `ALLOW_MUTATE` | `false` (default), `true` | While `false` the Run step is disabled. The dry-run uses `validate_only` and writes nothing. |
| `AUTH_MODE` | `static` (local demo), `basic` (Cloud Run) | `static` refuses to start on Cloud Run or when `ENV!=local`. `basic` needs your own `APP_USER`/`APP_PASSWORD` (12+ chars), `SECRET_KEY`, real `ADMIN_EMAILS`. |
| `ADS_ACCESS` | `shared` (default), `user` | `user`: after the basic login each person grants their own Google Ads access on a Google consent screen; accounts shown are only theirs. |
| `GOOGLE_ADS_*` | developer token, client id/secret, refresh token | Fixed Google Ads identity for Cloud Run (see [DEPLOY.md](DEPLOY.md)). Locally the yaml is used when these are unset. |
| `ADMIN_EMAILS`, `ALLOWED_EMAILS`, `ALLOWED_DOMAINS` | comma-separated | Who may use this deployment. Admins edit the lists in `/admin`. |

## Tests

```bash
python -m pytest -q                       # unit + API tests (mock backend)
python tests/e2e_smoke.py <screenshot-dir>  # browser smoke test; needs the server on :8080 and `pip install playwright` (uses installed Edge)
```

## Safety

- New asset groups are always created `PAUSED`.
- Writes need `ALLOW_MUTATE=true` plus a typed confirmation of the asset group count.
- Each asset group is created in one atomic request. A policy rejection never leaves a half-built asset group.
- Secrets (`google-ads.yaml`, `.env`, key files) are gitignored. They are never logged, rendered into pages or put in audit entries.
- Every action is appended to `data/audit.jsonl`.

## Deploy

See [DEPLOY.md](DEPLOY.md). Cloud Run uses the basic login. Set `ADS_ACCESS=user` for per-user Google consent. IAP is intentionally not implemented.
