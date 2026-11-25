"""IAP auth - NOT IMPLEMENTED (deliberately out of scope; deployments use AUTH_MODE=basic)."""
from __future__ import annotations


class IapAuthProvider:
    def __init__(self, *a, **k):
        raise NotImplementedError("IAP auth is not implemented; use AUTH_MODE=basic")
