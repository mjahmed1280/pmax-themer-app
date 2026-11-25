"""Cloud Build pre-flight: fail early, with ALL problems listed, before building anything.

Runs inside the cloud-sdk builder as the build service account. Checks (secret values are never printed):
  1. the 3 secrets are readable and valid (injected as env by cloudbuild.yaml)
  2. the build identity holds the IAM permissions the pipeline needs (via testIamPermissions, which
     needs no permission itself), and says which role to grant when one is missing
  3. the region works for Cloud Run
Usage: python3 scripts/preflight.py PROJECT_ID REGION RUN_SA_EMAIL AR_REPO LOGS_BUCKET
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

project, region, run_sa, ar_repo, logs_bucket = sys.argv[1:6]
problems: list[str] = []


def sh(*cmd: str) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


_, who = sh("gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)")
print(f"Build identity : {who or 'unknown'}")
print(f"Runtime SA     : {run_sa}")
rc, token = sh("gcloud", "auth", "print-access-token")


def test_perms(url: str, perms: list[str]) -> set[str] | None:
    req = urllib.request.Request(
        url, data=json.dumps({"permissions": perms}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return set(json.load(r).get("permissions", []))
    except (urllib.error.URLError, ValueError) as e:
        problems.append(f"could not test permissions at {url.split('/v')[0]}: {e}")
        return None


# ---- 1. secrets -------------------------------------------------------
gads = os.environ.get("GADS_JSON", "")
if not gads:
    problems.append("secret 'gads-credentials' is missing/empty or the build SA lacks Secret Manager Secret Accessor on it")
else:
    try:
        d = json.loads(gads)
        miss = [k for k in ("developer_token", "client_id", "client_secret", "refresh_token") if not d.get(k)]
        if miss:
            problems.append("secret 'gads-credentials' JSON is missing keys: " + ", ".join(miss))
    except ValueError:
        problems.append("secret 'gads-credentials' is not valid JSON")
if len(os.environ.get("APP_PW", "")) < 12:
    problems.append("secret 'pmax-app-password' is missing or shorter than 12 characters")
if len(os.environ.get("SECRET_KEY_VAL", "")) < 32:
    problems.append("secret 'pmax-secret-key' is missing or shorter than 32 characters")

# ---- 2. IAM -----------------------------------------------------------
need_project = {
    "run.services.create": "roles/run.admin",
    "run.services.update": "roles/run.admin",
    "run.services.setIamPolicy": "roles/run.admin (needed for --allow-unauthenticated)",
    "secretmanager.versions.access": "roles/secretmanager.secretAccessor",
}
# Artifact Registry: if the repo exists check upload on it, otherwise we must be able to create it.
rc, out = sh("gcloud", "artifacts", "repositories", "describe", ar_repo, f"--location={region}", f"--project={project}")
repo_exists = rc == 0
repo_denied = rc != 0 and ("PERMISSION_DENIED" in out or "does not have permission" in out)
if repo_denied:
    problems.append(f"no access to Artifact Registry repo '{ar_repo}' -> grant roles/artifactregistry.writer on that repo to the build identity")
elif not repo_exists:
    need_project["artifactregistry.repositories.create"] = "roles/artifactregistry.admin (repo does not exist yet)"

if token:
    got = test_perms(f"https://cloudresourcemanager.googleapis.com/v1/projects/{project}:testIamPermissions",
                     list(need_project))
    if got is not None:
        for perm, role in need_project.items():
            if perm not in got:
                problems.append(f"missing permission {perm} -> grant {role} to the build identity")

    if repo_exists:
        got = test_perms(f"https://artifactregistry.googleapis.com/v1/projects/{project}/locations/{region}"
                         f"/repositories/{ar_repo}:testIamPermissions", ["artifactregistry.repositories.uploadArtifacts"])
        if got is not None and "artifactregistry.repositories.uploadArtifacts" not in got:
            problems.append("missing permission artifactregistry.repositories.uploadArtifacts -> "
                            "grant roles/artifactregistry.writer on the repo (or project) to the build identity")

    try:   # logs bucket must be writable (GCS_ONLY logging, no Logs Writer role needed)
        req = urllib.request.Request(
            f"https://storage.googleapis.com/storage/v1/b/{logs_bucket}/iam/testPermissions?permissions=storage.objects.create",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            if "storage.objects.create" not in json.load(r).get("permissions", []):
                problems.append(f"build identity cannot write to logs bucket gs://{logs_bucket} -> grant roles/storage.objectAdmin on it")
    except (urllib.error.URLError, ValueError) as e:
        problems.append(f"logs bucket gs://{logs_bucket} not reachable ({e}); create it (see DEPLOY.md)")

    got = test_perms(f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{run_sa}:testIamPermissions",
                     ["iam.serviceAccounts.actAs"])
    if got is not None and "iam.serviceAccounts.actAs" not in got:
        problems.append(f"missing permission iam.serviceAccounts.actAs -> grant roles/iam.serviceAccountUser "
                        f"to the build identity on {run_sa} (Cloud Run Admin does not include it)")
else:
    problems.append("could not obtain an access token for the build identity")

# ---- 3. region --------------------------------------------------------
rc, out = sh("gcloud", "run", "services", "list", f"--region={region}", f"--project={project}", "--limit=1")
if rc != 0:
    problems.append(f"cannot list Cloud Run services in region '{region}' (wrong region, API disabled, or no run.* access)")

print()
if problems:
    print("PREFLIGHT FAILED:")
    for p in problems:
        print("  - " + p)
    sys.exit(1)
print("PREFLIGHT OK: secrets valid, IAM sufficient, region reachable")
