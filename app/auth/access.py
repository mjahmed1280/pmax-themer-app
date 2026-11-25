"""Admin-controlled access policy.

Each deployment (e.g. one per client GCP project) decides who may use it:
  * ADMIN_EMAILS  (env)  - always admins, can manage the lists below
  * ALLOWED_EMAILS / ALLOWED_DOMAINS (env) - seed lists
  * the admin UI adds/removes extra emails and domains, persisted in a store.

Local store = JSON file. For Cloud Run swap FileAccessStore for a Firestore
implementation of the same two methods (load/save); nothing else changes.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.auth.base import User


class FileAccessStore:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> dict:
        try:
            return json.loads(self.path.read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"emails": [], "domains": []}

    def save(self, data: dict) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, indent=2), "utf-8")
            tmp.replace(self.path)


def _norm(s: str) -> str:
    return (s or "").strip().lower()


class AccessPolicy:
    def __init__(self, store, admins: list[str], seed_emails: list[str], seed_domains: list[str]):
        self.store = store
        self.admins = {_norm(a) for a in admins}
        self.seed_emails = {_norm(e) for e in seed_emails}
        self.seed_domains = {_norm(d).lstrip("@") for d in seed_domains}

    # --- queries -----------------------------------------------------
    def _lists(self) -> tuple[set[str], set[str]]:
        d = self.store.load()
        return (
            self.seed_emails | {_norm(e) for e in d.get("emails", [])},
            self.seed_domains | {_norm(x).lstrip("@") for x in d.get("domains", [])},
        )

    def is_admin(self, email: str) -> bool:
        return _norm(email) in self.admins

    def is_allowed(self, email: str) -> bool:
        email = _norm(email)
        if not email or "@" not in email:
            return False
        if email in self.admins:
            return True
        emails, domains = self._lists()
        return email in emails or email.split("@", 1)[1] in domains

    def resolve(self, email: str, name: str = "") -> User | None:
        if not self.is_allowed(email):
            return None
        return User(email=_norm(email), name=name, is_admin=self.is_admin(email))

    # --- admin mutations --------------------------------------------
    def snapshot(self) -> dict:
        d = self.store.load()
        return {
            "admins": sorted(self.admins),
            "seed_emails": sorted(self.seed_emails),
            "seed_domains": sorted(self.seed_domains),
            "emails": sorted({_norm(e) for e in d.get("emails", [])}),
            "domains": sorted({_norm(x).lstrip("@") for x in d.get("domains", [])}),
        }

    def add(self, kind: str, value: str) -> None:
        value = _norm(value).lstrip("@") if kind == "domain" else _norm(value)
        if kind == "email" and ("@" not in value or " " in value):
            raise ValueError("Invalid email")
        if kind == "domain" and ("." not in value or " " in value or "@" in value):
            raise ValueError("Invalid domain")
        if kind not in ("email", "domain"):
            raise ValueError("kind must be 'email' or 'domain'")
        d = self.store.load()
        key = "emails" if kind == "email" else "domains"
        items = {_norm(x) for x in d.get(key, [])}
        items.add(value)
        d[key] = sorted(items)
        d.setdefault("emails", [])
        d.setdefault("domains", [])
        self.store.save(d)

    def remove(self, kind: str, value: str) -> None:
        value = _norm(value).lstrip("@")
        if kind not in ("email", "domain"):
            raise ValueError("kind must be 'email' or 'domain'")
        d = self.store.load()
        key = "emails" if kind == "email" else "domains"
        d[key] = sorted({_norm(x) for x in d.get(key, [])} - {value})
        d.setdefault("emails", [])
        d.setdefault("domains", [])
        self.store.save(d)
