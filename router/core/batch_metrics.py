from __future__ import annotations

from typing import Any


def _latency_summary(values: list[int]) -> dict[str, Any]:
    return {
        "count": len(values),
        "min_ms": min(values),
        "max_ms": max(values),
        "avg_ms": round(sum(values) / len(values), 2),
    }


def _pool_status(attempt_count: int, success_count: int) -> str:
    if attempt_count <= 0:
        return "unproven"
    success_rate = success_count / attempt_count
    if success_count > 0 and success_rate >= 0.5:
        return "ready_recent_success"
    if success_count > 0:
        return "pool_risk_low_success_rate"
    return "pool_risk_no_recent_success"


def _artifact_quality(attempt: dict[str, Any]) -> dict[str, Any]:
    metrics = attempt.get("artifact_metrics")
    if not isinstance(metrics, dict):
        return {
            "known": False,
            "valid_success": None,
        }
    artifact_count = int(metrics.get("artifact_count") or 0)
    valid_image_count = int(metrics.get("valid_image_count") or 0)
    invalid_image_count = int(metrics.get("invalid_image_count") or 0)
    return {
        "known": True,
        "valid_success": artifact_count > 0 and valid_image_count > 0 and invalid_image_count == 0,
    }


def _effective_ok(attempt: dict[str, Any]) -> bool:
    ok = bool(attempt.get("ok"))
    if not ok:
        return False
    artifact_quality = _artifact_quality(attempt)
    if not artifact_quality["known"]:
        return ok
    return bool(artifact_quality["valid_success"])


def _effective_category(attempt: dict[str, Any]) -> str:
    category = str(attempt.get("error_category") or ("success" if attempt.get("ok") else "unknown"))
    artifact_quality = _artifact_quality(attempt)
    if bool(attempt.get("ok")) and artifact_quality["known"] and not artifact_quality["valid_success"]:
        return "invalid_artifact"
    return category


def summarize_batch_attempts(items: list[dict[str, Any]]) -> dict[str, Any]:
    provider_attempt_totals: dict[str, int] = {}
    pool_attempt_totals: dict[str, int] = {}
    provider_latency_ms: dict[str, list[int]] = {}
    pool_latency_ms: dict[str, list[int]] = {}
    provider_queue_wait_ms: dict[str, list[int]] = {}
    pool_queue_wait_ms: dict[str, list[int]] = {}
    provider_artifact_totals: dict[str, dict[str, int]] = {}
    pool_artifact_totals: dict[str, dict[str, int]] = {}
    error_category_totals: dict[str, int] = {}
    pool_error_category_totals: dict[str, dict[str, int]] = {}
    pool_success_totals: dict[str, int] = {}

    for item in items:
        for attempt in list(item.get("attempts") or []):
            provider = str(attempt.get("provider") or "unknown")
            pool_identity = str(attempt.get("pool_identity") or attempt.get("provider_group") or provider)
            latency_ms = attempt.get("latency_ms")
            scheduling_wait_ms = attempt.get("scheduling_wait_ms")
            metrics = attempt.get("artifact_metrics") or {}
            category = _effective_category(attempt)

            provider_attempt_totals[provider] = provider_attempt_totals.get(provider, 0) + 1
            pool_attempt_totals[pool_identity] = pool_attempt_totals.get(pool_identity, 0) + 1
            if _effective_ok(attempt):
                pool_success_totals[pool_identity] = pool_success_totals.get(pool_identity, 0) + 1
            error_category_totals[category] = error_category_totals.get(category, 0) + 1

            pool_errors = pool_error_category_totals.setdefault(pool_identity, {})
            pool_errors[category] = pool_errors.get(category, 0) + 1

            if isinstance(latency_ms, int):
                provider_latency_ms.setdefault(provider, []).append(latency_ms)
                pool_latency_ms.setdefault(pool_identity, []).append(latency_ms)
            if isinstance(scheduling_wait_ms, int):
                provider_queue_wait_ms.setdefault(provider, []).append(scheduling_wait_ms)
                pool_queue_wait_ms.setdefault(pool_identity, []).append(scheduling_wait_ms)
            if isinstance(metrics, dict):
                artifact_count = int(metrics.get("artifact_count") or 0)
                valid_count = int(metrics.get("valid_image_count") or 0)
                invalid_count = int(metrics.get("invalid_image_count") or 0)
                aspect_mismatch_count = int(metrics.get("aspect_ratio_mismatch_count") or 0)
                provider_artifacts = provider_artifact_totals.setdefault(
                    provider,
                    {
                        "artifact_count": 0,
                        "valid_image_count": 0,
                        "invalid_image_count": 0,
                        "aspect_ratio_mismatch_count": 0,
                    },
                )
                pool_artifacts = pool_artifact_totals.setdefault(
                    pool_identity,
                    {
                        "artifact_count": 0,
                        "valid_image_count": 0,
                        "invalid_image_count": 0,
                        "aspect_ratio_mismatch_count": 0,
                    },
                )
                provider_artifacts["artifact_count"] += artifact_count
                provider_artifacts["valid_image_count"] += valid_count
                provider_artifacts["invalid_image_count"] += invalid_count
                provider_artifacts["aspect_ratio_mismatch_count"] += aspect_mismatch_count
                pool_artifacts["artifact_count"] += artifact_count
                pool_artifacts["valid_image_count"] += valid_count
                pool_artifacts["invalid_image_count"] += invalid_count
                pool_artifacts["aspect_ratio_mismatch_count"] += aspect_mismatch_count

    pool_outcome_summary = {
        pool_identity: {
            "attempt_count": attempt_count,
            "success_count": pool_success_totals.get(pool_identity, 0),
            "success_rate": round(pool_success_totals.get(pool_identity, 0) / attempt_count, 4) if attempt_count else 0.0,
            "status": _pool_status(attempt_count, pool_success_totals.get(pool_identity, 0)),
            "errors": pool_error_category_totals.get(pool_identity, {}),
        }
        for pool_identity, attempt_count in pool_attempt_totals.items()
    }
    pool_outcome_status_counts: dict[str, int] = {}
    for row in pool_outcome_summary.values():
        status = str(row["status"])
        pool_outcome_status_counts[status] = pool_outcome_status_counts.get(status, 0) + 1

    return {
        "provider_attempt_totals": provider_attempt_totals,
        "pool_attempt_totals": pool_attempt_totals,
        "pool_outcome_summary": pool_outcome_summary,
        "pool_outcome_status_counts": pool_outcome_status_counts,
        "provider_latency_summary": {
            provider: _latency_summary(values)
            for provider, values in provider_latency_ms.items()
            if values
        },
        "pool_latency_summary": {
            pool_identity: _latency_summary(values)
            for pool_identity, values in pool_latency_ms.items()
            if values
        },
        "provider_queue_wait_summary": {
            provider: _latency_summary(values)
            for provider, values in provider_queue_wait_ms.items()
            if values
        },
        "pool_queue_wait_summary": {
            pool_identity: _latency_summary(values)
            for pool_identity, values in pool_queue_wait_ms.items()
            if values
        },
        "provider_artifact_summary": provider_artifact_totals,
        "pool_artifact_summary": pool_artifact_totals,
        "error_category_totals": error_category_totals,
        "pool_error_category_totals": pool_error_category_totals,
    }
