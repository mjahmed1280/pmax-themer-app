# Deploy to Cloud Run (basic login + fixed Google Ads credentials)

Model: one shared login (`APP_USER=ops` / `APP_PASSWORD`) and ONE dedicated Google Ads identity
(`ADS_ACCESS=shared`). Everyone who logs in sees what that identity can access. No consent screen or
redirect URI is needed. Per-user consent (`ADS_ACCESS=user`) is built and tested but parked (last section).

## The 3 secrets to create in Secret Manager (everything else is created by cloudbuild.yaml)
| Secret name | Content |
|---|---|
| `gads-credentials` | JSON, all fixed Google Ads credentials in one secret (below) |
| `pmax-app-password` | the shared login password, 12+ chars |
| `pmax-secret-key` | random string, 32+ chars (signs session cookies) |

`gads-credentials` format (the app reads it from `GOOGLE_ADS_CREDENTIALS_JSON`):
```json
{
  "developer_token": "…",
  "client_id": "…apps.googleusercontent.com",
  "client_secret": "GOCSPX-…",
  "refresh_token": "1//…",
  "login_customer_id": ""
}
```
`login_customer_id` is optional (leave out or empty; the app picks the right MCC per account).

```bash
PROJECT=celestial-feat-513918-q9
gcloud secrets create gads-credentials  --project $PROJECT --data-file=gads.json   # then delete gads.json
printf '%s' '<12+ char password>' | gcloud secrets create pmax-app-password --project $PROJECT --data-file=-
python -c "import secrets;print(secrets.token_urlsafe(48),end='')" | gcloud secrets create pmax-secret-key --project $PROJECT --data-file=-
```
If the OAuth client's consent screen is External + *Testing*, its refresh token expires after 7 days.

## Identity: one service account builds AND runs it
`sa-gads-ai-runner@celestial-feat-513918-q9.iam.gserviceaccount.com`
- Set it as the **Service account** on the Cloud Build trigger.
- cloudbuild.yaml deploys the service with `--service-account` = the same SA.
- It already has: Cloud Build Admin, Cloud Run Admin, Cloud Run Builder, Secret Manager Secret Accessor, Storage Object Admin.
- Storage Object Admin does not cover Artifact Registry (only the old gcr.io buckets), and Cloud Run Admin does not include "act as a service account".

### A. Self-service (needs only roles the deploying human already has: Artifact Registry Admin, Storage Admin)
```bash
PROJECT=celestial-feat-513918-q9; REGION=asia-south1
SA=serviceAccount:sa-gads-ai-runner@$PROJECT.iam.gserviceaccount.com
# 1. Artifact Registry repo + let the runner push to it (resource-level grant, no project IAM admin needed)
gcloud artifacts repositories create pmax-themer --repository-format=docker --location=$REGION --project=$PROJECT
gcloud artifacts repositories add-iam-policy-binding pmax-themer --location=$REGION --project=$PROJECT \
  --member=$SA --role=roles/artifactregistry.writer
# 2. Build-log bucket (the runner already has Storage Object Admin; this avoids needing Logs Writer)
gcloud storage buckets create gs://$PROJECT-pmax-build-logs --location=$REGION --project=$PROJECT
# 3. The 3 secrets (see above)
```

### B. Needs the client's IT (project/SA IAM admin) - ONE grant
```bash
gcloud iam service-accounts add-iam-policy-binding sa-gads-ai-runner@$PROJECT.iam.gserviceaccount.com \
  --member=serviceAccount:sa-gads-ai-runner@$PROJECT.iam.gserviceaccount.com --role=roles/iam.serviceAccountUser
```
(Cloud Run only lets a deployer attach/run as a service account if the deployer may act as it. Here the deployer and the runtime identity are the same SA.)

### C. Questions for IT
- Does an org policy block public Cloud Run (`iam.allowedPolicyMemberDomains` / "allUsers" not allowed)? Check:
  `gcloud org-policies describe iam.allowedPolicyMemberDomains --project=$PROJECT --effective` and
  `gcloud run services get-iam-policy causmos-service --region=$REGION` (is `allUsers` there on the existing service?).
  If public access is blocked, the deploy succeeds but the login page returns 403.
- GitHub: the trigger needs the Cloud Build GitHub connection to `mjahmed1280/pmax-themer-app` (a personal GitHub account). IT may prefer the repo inside the client's GitHub org.

`scripts/preflight.py` (step 1 of the build) verifies A and B with `testIamPermissions` and lists every gap in one failure.

## What cloudbuild.yaml does on every push to `main`
1. **preflight**: the 3 secrets exist and are valid (JSON has all 4 keys, password 12+, key 32+), Cloud Build can reach Cloud Run in the region.
2. **test**: pytest.
3. **ensure-artifact-repo**: creates the Artifact Registry docker repo if missing.
4. **build / push**: image tagged with the commit SHA.
5. **deploy**: Cloud Run, `--max-instances=1`, `ALLOW_MUTATE=false`, secrets mounted.
6. **smoke**: `GET /login` must return 200.

Trigger: Cloud Build -> Triggers -> connect `mjahmed1280/pmax-themer-app`, event = push to `main`, config = `cloudbuild.yaml`.
Check `_REGION` (default `asia-south1`) matches where you want it.
After the first deploy: open the URL, log in as `ops`, run a dry-run against a real account, then set `_ALLOW_MUTATE` to `"true"` if wanted.

## Limits to know
- **max-instances 1**: the login throttle, `data/access.json` and `data/audit.jsonl` are per-container and reset on restart.
- Audit entries are also printed to stdout; Cloud Logging keeps them (filter `jsonPayload.audit`).
- One shared Google Ads identity and one login: no per-user audit trail. The identity currently sees ~130 accounts across many clients; restrict it before wider use.
- The app refuses to start if `APP_PASSWORD` is default/short, `SECRET_KEY` is empty, `ADMIN_EMAILS` is the placeholder, or the Ads credentials are missing.
- The atomic create is only verified against the mock: dry-run on the real account first.

## Parked: per-user consent (ADS_ACCESS=user)
Needs an OAuth *Web* client with redirect URI `https://<service-url>/oauth/callback`, the consent screen edited
(scopes openid/email/profile/adwords; Internal, or External + test users), and env `ADS_ACCESS=user`,
`OAUTH_REDIRECT_URI`, `GOOGLE_OAUTH_CLIENT_JSON` (the downloaded client JSON as a secret) plus the developer token.
Each user's refresh token lives encrypted in their session cookie.
