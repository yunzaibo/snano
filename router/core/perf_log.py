from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from router.core.batch_metrics import summarize_batch_attempts
from router.core.dedupe import request_hash
from router.core.models import GenerationRequest, GenerationResult
from router.core.provider_registry import provider_pool_metadata_for
from router.core.redaction import redact_text, redact_value
from router.core.remediation_events import remediation_summary
from router.core.request_metrics import reference_image_metrics
from router.core.response_contract import response_contract_signal


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERF_LOG_PATH = PROJECT_ROOT / "logs" / "performance.jsonl"
_WRITE_LOCK = threading.Lock()


def resolve_perf_log_path(explicit_path: str | None = None) -> str:
    if explicit_path:
        return explicit_path
    return os.environ.get("MIR_PERF_LOG_FILE", str(DEFAULT_PERF_LOG_PATH))


def _append_jsonl(path: str, payload: dict[str, Any]) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _WRITE_LOCK:
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(line)


def append_generation_result(
    request: GenerationRequest,
    result: GenerationResult,
    *,
    perf_log_path: str | None = None,
) -> str:
    resolved_path = resolve_perf_log_path(perf_log_path)
    request_metadata = redact_value(dict(request.metadata or {}))
    ref_metrics = request_metadata.get("referenceMetrics") or reference_image_metrics(request.reference_images)
    pool_metadata = provider_pool_metadata_for(result.provider)
    payload = {
        "event": "generation_result",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "provider": result.provider,
        **pool_metadata,
        "model": result.model,
        "ok": result.ok,
        "request_hash": request_hash(request),
        "job_type": request.job_type,
        "request_type": request.request_type,
        "reference_count": len(request.reference_images),
        "reference_metrics": ref_metrics,
        "aspect_ratio": request.aspect_ratio,
        "size": request.size,
        "quality": request.quality,
        "routing_mode": request.routing_mode,
        "race_providers": list(request.race_providers),
        "latency_ms": result.latency_ms,
        "timings_ms": dict(result.timings_ms or {}),
        "artifact_count": len(result.artifact_paths),
        "artifact_paths": list(result.artifact_paths),
        "artifact_metrics": dict(result.artifact_metrics or {}),
        "raw_response_path": result.raw_response_path,
        "provider_response_contract": response_contract_signal(result.provider_response),
        "error_code": result.error_code,
        "error_message": redact_text(result.error_message),
        "request_metadata": request_metadata,
    }
    _append_jsonl(resolved_path, payload)
    return resolved_path


def append_batch_summary(
    summary: dict[str, Any],
    *,
    perf_log_path: str | None = None,
) -> str:
    resolved_path = resolve_perf_log_path(perf_log_path)
    items = list(summary.get("items") or [])
    metrics = summarize_batch_attempts(items)
    remediation_events = list(summary.get("remediation_events") or summary.get("recovery_events") or [])
    route_decisions: dict[str, Any] = {}

    for item in items:
        item_id = str(item.get("item_id"))
        attempts = list(item.get("attempts") or [])
        skipped = list(item.get("skipped_providers") or [])
        compact_attempts = []
        for attempt in attempts:
            provider = str(attempt.get("provider"))
            latency_ms = attempt.get("latency_ms")
            compact_attempts.append({
                "provider": provider,
                "provider_group": attempt.get("provider_group"),
                "pool_identity": attempt.get("pool_identity"),
                "station_id": attempt.get("station_id"),
                "account_id": attempt.get("account_id"),
                "account_group": attempt.get("account_group"),
                "credential_id": attempt.get("credential_id"),
                "route_id": attempt.get("route_id"),
                "retry_index": attempt.get("retry_index"),
                "ok": attempt.get("ok"),
                "latency_ms": latency_ms,
                "scheduling_wait_ms": attempt.get("scheduling_wait_ms"),
                "timings_ms": dict(attempt.get("timings_ms") or {}),
                "error_category": attempt.get("error_category"),
                "routing_action": attempt.get("routing_action"),
                "cooldown_seconds": attempt.get("cooldown_seconds"),
                "remediation_hint": attempt.get("remediation_hint"),
                "error_code": attempt.get("error_code"),
                "error_message": redact_text(attempt.get("error_message")),
                "provider_response_contract": dict(attempt.get("provider_response_contract") or {}),
                "artifact_metrics": dict(attempt.get("artifact_metrics") or {}),
                "reference_used_count": attempt.get("reference_used_count"),
                "reference_downgraded": attempt.get("reference_downgraded"),
                "reference_original_total_bytes": attempt.get("reference_original_total_bytes"),
                "reference_used_total_bytes": attempt.get("reference_used_total_bytes"),
                "reference_original_estimated_base64_bytes": attempt.get("reference_original_estimated_base64_bytes"),
                "reference_used_estimated_base64_bytes": attempt.get("reference_used_estimated_base64_bytes"),
                "reference_missing_count": attempt.get("reference_missing_count"),
            })

        route_decisions[item_id] = {
            "status": item.get("status"),
            "routing_policy": item.get("routing_policy"),
            "effective_routing_mode": item.get("effective_routing_mode"),
            "planned_providers": list(item.get("planned_providers") or []),
            "selected_provider": item.get("selected_provider"),
            "selected_provider_group": item.get("selected_provider_group"),
            "selected_provider_pool_identity": item.get("selected_provider_pool_identity"),
            "latency_ms": item.get("latency_ms"),
            "attempt_count": len(attempts),
            "attempted_providers": list(item.get("attempted_providers") or []),
            "attempts": compact_attempts,
            "skipped_providers": [
                {
                    "provider": row.get("provider"),
                    "provider_group": row.get("provider_group"),
                    "pool_identity": row.get("pool_identity"),
                    "station_id": row.get("station_id"),
                    "account_id": row.get("account_id"),
                    "account_group": row.get("account_group"),
                    "credential_id": row.get("credential_id"),
                    "route_id": row.get("route_id"),
                    "reason": row.get("reason"),
                    "error_category": row.get("error_category"),
                    "cooldown_until": row.get("cooldown_until"),
                }
                for row in skipped
            ],
            "race_requested_providers": list(item.get("race_requested_providers") or []),
            "race_launched_providers": list(item.get("race_launched_providers") or []),
            "race_completed_providers": list(item.get("race_completed_providers") or []),
        }

    payload = {
        "event": "batch_summary",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": summary.get("schema_version"),
        "dry_run": summary.get("dry_run"),
        "job_type": summary.get("job_type"),
        "input_path": summary.get("input_path"),
        "success_count": summary.get("success_count"),
        "failure_count": summary.get("failure_count"),
        "max_workers": summary.get("max_workers"),
        "max_retries_per_provider": summary.get("max_retries_per_provider"),
        "retry_delay_seconds": summary.get("retry_delay_seconds"),
        "provider_state_path": summary.get("provider_state_path"),
        "items_total": len(items),
        **metrics,
        "remediation_events": redact_value(remediation_events),
        "remediation_summary": summary.get("remediation_summary") or remediation_summary(remediation_events),
        "secret_values_included": False,
        "route_decisions": route_decisions,
    }
    _append_jsonl(resolved_path, payload)
    return resolved_path
