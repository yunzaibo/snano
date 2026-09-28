from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from router.core.redaction import redact_text
from router.core.scheduler import ProviderState


SCHEMA_VERSION = "provider-health/v1"


class ProviderHealthStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self, providers: Iterable[str]) -> dict[str, ProviderState]:
        stored = self._read()
        provider_rows = stored.get("providers", {}) if isinstance(stored, dict) else {}
        states: dict[str, ProviderState] = {}
        for provider in providers:
            row = provider_rows.get(provider, {}) if isinstance(provider_rows, dict) else {}
            states[provider] = ProviderState(
                healthy=bool(row.get("healthy", True)),
                cooldown_until=float(row.get("cooldown_until") or 0.0),
                weight=int(row.get("weight") or 1),
                cooldown_reason=row.get("cooldown_reason"),
                last_error_category=row.get("last_error_category"),
                last_error_message=row.get("last_error_message"),
                last_success_at=row.get("last_success_at"),
                last_failure_at=row.get("last_failure_at"),
                last_recovery_signal=row.get("last_recovery_signal"),
                consecutive_failures=int(row.get("consecutive_failures") or 0),
            )
        return states

    def save(self, states: dict[str, ProviderState]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "updated_at": time.time(),
            "providers": {
                provider: self._safe_state_row(state)
                for provider, state in sorted(states.items())
            },
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def snapshot(self, states: dict[str, ProviderState]) -> dict[str, dict[str, Any]]:
        return {
            provider: self._safe_state_row(state)
            for provider, state in sorted(states.items())
        }

    @staticmethod
    def apply_success(state: ProviderState, *, signal: str = "passive_success") -> None:
        state.healthy = True
        state.cooldown_until = 0.0
        state.cooldown_reason = None
        state.last_error_category = None
        state.last_error_message = None
        state.last_success_at = time.time()
        state.last_recovery_signal = signal
        state.consecutive_failures = 0

    @staticmethod
    def apply_failure(
        state: ProviderState,
        *,
        category: str,
        message: str | None,
        cooldown_until: float | None = None,
        cooldown_reason: str | None = None,
    ) -> None:
        state.last_failure_at = time.time()
        state.last_error_category = category
        state.last_error_message = redact_text(message)
        state.consecutive_failures += 1
        if cooldown_until is not None:
            state.cooldown_until = max(state.cooldown_until, cooldown_until)
        if cooldown_reason is not None:
            state.cooldown_reason = cooldown_reason

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if data.get("schema_version") != SCHEMA_VERSION:
            return {}
        return data

    @staticmethod
    def _safe_state_row(state: ProviderState) -> dict[str, Any]:
        row = asdict(state)
        row.pop("inflight", None)
        return row
