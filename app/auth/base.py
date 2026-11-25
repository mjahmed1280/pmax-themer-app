"""Auth interfaces.

Two separate concerns:
  * AuthProvider      - who is the user (static login locally, IAP in prod)
  * AccessPolicy      - is that user allowed into THIS deployment (admin-controlled)
  * CredentialProvider- which Google Ads credentials to use for that user
                        (local yaml now, per-user OAuth refresh token in prod)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Request


@dataclass(frozen=True)
class User:
    email: str
    name: str = ""
    is_admin: bool = False

    def to_dict(self) -> dict:
        return {"email": self.email, "name": self.name or self.email, "is_admin": self.is_admin}


class AuthProvider(Protocol):
    def current_identity(self, request: Request) -> tuple[str, str] | None:
        """Return (email, display_name) of the verified caller, or None."""
