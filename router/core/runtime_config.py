from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    output_dir: str
    batch_dir: str
    ledger_dir: str
    perf_log_path: str
    provider_state_path: str
    queue_dir: str
    provider_lock_dir: str
    remediation_audit_path: str
    max_workers: int | None
    max_retries_per_provider: int
    retry_delay_seconds: float

    @classmethod
    def default(cls) -> "RuntimeConfig":
        max_workers_raw = os.environ.get("MIR_MAX_WORKERS", "0")
        max_workers = int(max_workers_raw or "0")
        return cls(
            output_dir=os.environ.get("MIR_OUTPUT_DIR", str(PROJECT_ROOT / "data" / "artifacts")),
            batch_dir=os.environ.get("MIR_BATCH_DIR", str(PROJECT_ROOT / "data" / "batches")),
            ledger_dir=os.environ.get("MIR_LEDGER_DIR", str(PROJECT_ROOT / "data" / "ledger")),
            perf_log_path=os.environ.get("MIR_PERF_LOG_FILE", str(PROJECT_ROOT / "logs" / "performance.jsonl")),
            provider_state_path=os.environ.get(
                "MIR_PROVIDER_STATE_FILE",
                str(PROJECT_ROOT / "data" / "provider-health.json"),
            ),
            queue_dir=os.environ.get("MIR_QUEUE_DIR", str(PROJECT_ROOT / "data" / "queue")),
            provider_lock_dir=os.environ.get("MIR_PROVIDER_LOCK_DIR", str(PROJECT_ROOT / "data" / "provider-locks")),
            remediation_audit_path=os.environ.get(
                "MIR_REMEDIATION_AUDIT_FILE",
                str(PROJECT_ROOT / "logs" / "remediation-audit.jsonl"),
            ),
            max_workers=max_workers or None,
            max_retries_per_provider=int(os.environ.get("MIR_MAX_RETRIES_PER_PROVIDER", "1")),
            retry_delay_seconds=float(os.environ.get("MIR_RETRY_DELAY_SECONDS", "2.0")),
        )
