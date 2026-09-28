from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROVIDER_LEASE_SCHEMA_VERSION = "provider-lease/v1"


@dataclass(frozen=True, slots=True)
class ProviderLease:
    provider: str
    path: Path
    token: str


class ProviderLeaseManager:
    def __init__(self, lock_dir: str | Path, *, lease_timeout_seconds: float = 7200.0):
        self.lock_dir = Path(lock_dir)
        self.lease_timeout_seconds = lease_timeout_seconds
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def acquire(
        self,
        provider: str,
        *,
        max_concurrency: int,
        task_id: str | None = None,
        wait_timeout_seconds: float = 0.0,
        poll_seconds: float = 0.05,
    ) -> ProviderLease | None:
        deadline = time.time() + max(0.0, wait_timeout_seconds)
        slots = max(1, int(max_concurrency or 1))
        while True:
            self._cleanup_stale(provider)
            for index in range(slots):
                path = self._slot_path(provider, index)
                token = uuid.uuid4().hex
                payload = {
                    "schema_version": PROVIDER_LEASE_SCHEMA_VERSION,
                    "provider": provider,
                    "slot": index,
                    "token": token,
                    "pid": os.getpid(),
                    "task_id": task_id,
                    "acquired_at": time.time(),
                    "expires_at": time.time() + self.lease_timeout_seconds,
                }
                try:
                    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    continue
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                return ProviderLease(provider=provider, path=path, token=token)
            if time.time() >= deadline:
                return None
            time.sleep(poll_seconds)

    def release(self, lease: ProviderLease | None) -> None:
        if lease is None or not lease.path.exists():
            return
        try:
            data = json.loads(lease.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        if data.get("token") != lease.token:
            return
        try:
            lease.path.unlink()
        except FileNotFoundError:
            return

    def active_leases(self, provider: str) -> list[dict[str, Any]]:
        root = self._provider_dir(provider)
        if not root.exists():
            return []
        rows: list[dict[str, Any]] = []
        for path in sorted(root.glob("lease-*.json")):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            row["path"] = str(path)
            rows.append(row)
        return rows

    def _cleanup_stale(self, provider: str) -> None:
        now = time.time()
        for row in self.active_leases(provider):
            expires_at = float(row.get("expires_at") or 0)
            if expires_at > now:
                continue
            path = Path(str(row.get("path")))
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    def _slot_path(self, provider: str, index: int) -> Path:
        root = self._provider_dir(provider)
        root.mkdir(parents=True, exist_ok=True)
        return root / f"lease-{index:03d}.json"

    def _provider_dir(self, provider: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", provider.strip().lower()) or "unknown"
        while ".." in safe:
            safe = safe.replace("..", "_")
        safe = safe.strip("._-") or "unknown"
        return self.lock_dir / safe
