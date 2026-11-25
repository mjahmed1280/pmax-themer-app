"""Append-only audit log (JSON lines). Never write secrets here."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def write(self, user: str, action: str, **fields) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "user": user, "action": action, **fields}
        # stdout copy: on Cloud Run the disk is ephemeral, Cloud Logging keeps the durable record
        print(json.dumps({"audit": rec}, ensure_ascii=False), file=sys.stdout, flush=True)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 50) -> list[dict]:
        try:
            lines = self.path.read_text("utf-8").splitlines()[-limit:]
        except FileNotFoundError:
            return []
        out = []
        for ln in reversed(lines):
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
        return out
