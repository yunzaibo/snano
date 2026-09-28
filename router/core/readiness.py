from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from router.core.perf_score import pool_correlation_signals, pool_scores, pool_status, pool_status_counts, provider_scores
from router.core.provider_registry import provider_startup_diagnostics, provider_tier_for
from router.core.provider_state_store import ProviderHealthStore
from router.core.remediation_events import remediation_events_from_readiness, remediation_summary
from router.core.scheduler import ProviderState


READINESS_SCHEMA_VERSION = "lane-readiness/v1"


def _read_events(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    candidate = Path(path)
    if not candidate.exists():
        return []
    files = sorted(candidate.glob("*.jsonl")) if candidate.is_dir() else [candidate]
    events: list[dict[str, Any]] = []
    for file_path in files:
        if not file_path.exists():
            continue
        for line in file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def _status_for(row: dict[str, Any], state: ProviderState, score: dict[str, Any] | None, now: float) -> str:
    if not bool(row.get("enabled", True)):
        return "disabled"
    if not bool(row.get("key_present")):
        return "missing_credential"
    if state.cooldown_until > now:
        return "cooldown"
    if score and int(score.get("success_count") or 0) > 0:
        return "ready_recent_success"
    if score and int(score.get("attempt_count") or 0) > 0:
        return "needs_real_success"
    return "configured_unproven"


def _performance_summary(score: dict[str, Any] | None) -> dict[str, Any]:
    score = score or {}
    return {
        "attempt_count": int(score.get("attempt_count") or 0),
        "success_count": int(score.get("success_count") or 0),
        "success_rate": score.get("success_rate"),
        "p50_latency_ms": score.get("p50_latency_ms"),
        "p90_latency_ms": score.get("p90_latency_ms"),
        "errors": score.get("errors") or {},
        "artifact_count": int(score.get("artifact_count") or 0),
        "valid_image_count": int(score.get("valid_image_count") or 0),
        "invalid_image_count": int(score.get("invalid_image_count") or 0),
        "aspect_ratio_mismatch_count": int(score.get("aspect_ratio_mismatch_count") or 0),
        "ignored_fast_success_count": int(score.get("ignored_fast_success_count") or 0),
        "min_real_success_latency_ms": score.get("min_real_success_latency_ms"),
    }


def _performance_warnings(score: dict[str, Any] | None, *, latency_budget_seconds: int | None = None) -> list[str]:
    warnings: list[str] = []
    score = score or {}
    success_count = int(score.get("success_count") or 0)
    ignored_fast_success_count = int(score.get("ignored_fast_success_count") or 0)
    if success_count <= 0:
        warnings.append("真实成功样本不足：缺少 >=1000ms 的有效生图成功记录，暂不建议把该线路作为性能最优依据。")
    if ignored_fast_success_count > 0:
        warnings.append(f"已忽略 {ignored_fast_success_count} 条 <1000ms 的成功记录，疑似测试或模拟数据。")
    p50 = score.get("p50_latency_ms")
    if latency_budget_seconds and isinstance(p50, int) and p50 > latency_budget_seconds * 1000:
        warnings.append(f"P50 耗时超过 preset 预算 {latency_budget_seconds}s。")
    return warnings


def build_readiness_report(
    *,
    perf_log_path: str | None = None,
    provider_state_path: str | None = None,
    providers: list[str] | None = None,
    latency_budget_seconds: int | None = None,
) -> dict[str, Any]:
    """Build a no-network readiness report without exposing secret values."""
    requested = {name.strip().lower() for name in providers or []}
    rows = [
        row
        for row in provider_startup_diagnostics()
        if not requested or str(row.get("provider", "")).strip().lower() in requested
    ]
    names = [str(row["provider"]) for row in rows]
    states = (
        ProviderHealthStore(provider_state_path).load(names)
        if provider_state_path
        else {name: ProviderState() for name in names}
    )
    events = _read_events(perf_log_path)
    score_rows = {
        str(row["provider"]): row
        for row in provider_scores(events)
    }
    pool_score_rows = pool_scores(events)
    now = time.time()

    lane_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    configured_pools: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = str(row["provider"])
        state = states.get(name, ProviderState())
        score = score_rows.get(name)
        status = _status_for(row, state, score, now)
        status_counts[status] = status_counts.get(status, 0) + 1
        cooldown_remaining = max(0, int(state.cooldown_until - now)) if state.cooldown_until else 0
        pool_identity = row.get("pool_identity") or row.get("provider_group")
        if pool_identity:
            configured = configured_pools.setdefault(str(pool_identity), {
                "pool_identity": str(pool_identity),
                "provider_group": row.get("provider_group"),
                "station_id": row.get("station_id"),
                "account_id": row.get("account_id"),
                "account_group": row.get("account_group"),
                "lanes": [],
            })
            configured["lanes"].append(name)
        lane_rows.append({
            "id": name,
            "is_lane": bool(row.get("is_lane")),
            "base_provider": row.get("base_provider") or name,
            "source_tier": row.get("source_tier") or provider_tier_for(name),
            "provider_group": row.get("provider_group"),
            "pool_identity": row.get("pool_identity") or row.get("provider_group"),
            "station_id": row.get("station_id"),
            "account_id": row.get("account_id"),
            "account_group": row.get("account_group"),
            "credential_id": row.get("credential_id"),
            "route_id": row.get("route_id"),
            "enabled": bool(row.get("enabled", True)),
            "credential_present": bool(row.get("key_present")),
            "base_url": row.get("base_url"),
            "model": row.get("model"),
            "capability_bucket": row.get("capability_bucket"),
            "max_reference_images": row.get("max_reference_images"),
            "max_concurrency": row.get("max_concurrency"),
            "status": status,
            "health": {
                "healthy": state.healthy,
                "cooldown_active": cooldown_remaining > 0,
                "cooldown_remaining_seconds": cooldown_remaining,
                "cooldown_reason": state.cooldown_reason,
                "last_error_category": state.last_error_category,
                "consecutive_failures": state.consecutive_failures,
                "last_success_at": state.last_success_at,
                "last_failure_at": state.last_failure_at,
            },
            "performance": _performance_summary(score),
            "performance_warnings": _performance_warnings(score, latency_budget_seconds=latency_budget_seconds),
        })

    pool_scores_by_identity = {str(row["pool_identity"]): row for row in pool_score_rows}
    pool_rows: list[dict[str, Any]] = []
    for pool_identity, configured in configured_pools.items():
        score = pool_scores_by_identity.get(pool_identity)
        pool_rows.append({
            **configured,
            "status": pool_status(score or {}),
            "performance": {
                **_performance_summary(score),
                "providers": (score or {}).get("providers") or [],
            },
            "performance_warnings": _performance_warnings(score, latency_budget_seconds=latency_budget_seconds),
        })
    for score in pool_score_rows:
        pool_identity = str(score["pool_identity"])
        if pool_identity in configured_pools:
            continue
        pool_rows.append({
            "pool_identity": pool_identity,
            "provider_group": (score.get("provider_groups") or [None])[0],
            "station_id": None,
            "account_id": None,
            "account_group": None,
            "lanes": score.get("providers") or [],
            "status": pool_status(score),
            "performance": {
                **_performance_summary(score),
                "providers": score.get("providers") or [],
            },
            "performance_warnings": _performance_warnings(score, latency_budget_seconds=latency_budget_seconds),
        })
    warning_count = sum(len(row.get("performance_warnings") or []) for row in lane_rows)
    remediation_events = remediation_events_from_readiness(lane_rows, now=now)
    return {
        "schema_version": READINESS_SCHEMA_VERSION,
        "generated_at": now,
        "network_calls": 0,
        "secret_values_included": False,
        "default_pool_assumption": "independent_by_website_account_group_or_lane_until_logs_prove_correlation",
        "pool_correlation_signals": pool_correlation_signals(events),
        "perf_log_path": perf_log_path,
        "provider_state_path": provider_state_path,
        "latency_budget_seconds": latency_budget_seconds,
        "performance_warning_count": warning_count,
        "remediation_events": remediation_events,
        "remediation_summary": remediation_summary(remediation_events),
        "status_counts": status_counts,
        "pool_status_counts": pool_status_counts(pool_rows),
        "pools": sorted(pool_rows, key=lambda row: str(row["pool_identity"])),
        "lanes": lane_rows,
    }
