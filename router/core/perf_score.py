from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any


MIN_REAL_SUCCESS_LATENCY_MS = 1000


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _event_timestamp(event: dict[str, Any]) -> float | None:
    raw = event.get("recorded_at")
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _within_window(event: dict[str, Any], *, now: float | None, window_seconds: float | None) -> bool:
    if window_seconds is None or window_seconds <= 0 or now is None:
        return True
    timestamp = _event_timestamp(event)
    if timestamp is None:
        return True
    return timestamp >= now - window_seconds


def _artifact_quality(payload: dict[str, Any]) -> dict[str, Any]:
    metrics = payload.get("artifact_metrics")
    if not isinstance(metrics, dict):
        return {
            "known": False,
            "artifact_count": 0,
            "valid_image_count": 0,
            "invalid_image_count": 0,
            "aspect_ratio_mismatch_count": 0,
            "valid_success": None,
        }
    artifact_count = int(metrics.get("artifact_count") or 0)
    valid_image_count = int(metrics.get("valid_image_count") or 0)
    invalid_image_count = int(metrics.get("invalid_image_count") or 0)
    aspect_ratio_mismatch_count = int(metrics.get("aspect_ratio_mismatch_count") or 0)
    return {
        "known": True,
        "artifact_count": artifact_count,
        "valid_image_count": valid_image_count,
        "invalid_image_count": invalid_image_count,
        "aspect_ratio_mismatch_count": aspect_ratio_mismatch_count,
        "valid_success": artifact_count > 0 and valid_image_count > 0 and invalid_image_count == 0,
    }


def _effective_ok(payload: dict[str, Any]) -> bool:
    ok = bool(payload.get("ok"))
    if not ok:
        return False
    artifact_quality = _artifact_quality(payload)
    if not artifact_quality["known"]:
        return ok
    return bool(artifact_quality["valid_success"])


def _real_success_latency_ms(payload: dict[str, Any]) -> int | None:
    latency_ms = payload.get("latency_ms")
    if not isinstance(latency_ms, int):
        return None
    if latency_ms < MIN_REAL_SUCCESS_LATENCY_MS:
        return None
    return latency_ms


def provider_scores(
    events: list[dict[str, Any]],
    *,
    window_seconds: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    if window_seconds is not None and now is None:
        now = datetime.now(timezone.utc).timestamp()
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "attempt_count": 0,
        "success_count": 0,
        "latencies": [],
        "errors": Counter(),
        "artifact_count": 0,
        "valid_image_count": 0,
        "invalid_image_count": 0,
        "aspect_ratio_mismatch_count": 0,
        "window_event_count": 0,
        "ignored_fast_success_count": 0,
    })

    for event in events:
        if event.get("event") != "generation_result":
            continue
        if not _within_window(event, now=now, window_seconds=window_seconds):
            continue
        provider = str(event.get("provider") or "")
        if not provider:
            continue
        row = rows[provider]
        row["window_event_count"] += 1
        row["attempt_count"] += 1
        artifact_quality = _artifact_quality(event)
        row["artifact_count"] += int(artifact_quality["artifact_count"])
        row["valid_image_count"] += int(artifact_quality["valid_image_count"])
        row["invalid_image_count"] += int(artifact_quality["invalid_image_count"])
        row["aspect_ratio_mismatch_count"] += int(artifact_quality["aspect_ratio_mismatch_count"])
        ok = _effective_ok(event)
        if ok:
            latency_ms = _real_success_latency_ms(event)
            if latency_ms is None:
                row["ignored_fast_success_count"] += 1
                continue
            row["success_count"] += 1
            row["latencies"].append(latency_ms)
        else:
            error_code = _failure_category(event)
            if bool(event.get("ok")) and artifact_quality["known"] and not artifact_quality["valid_success"]:
                error_code = "invalid_artifact"
            row["errors"][error_code] += 1

    scored: list[dict[str, Any]] = []
    for provider, row in rows.items():
        attempt_count = int(row["attempt_count"])
        success_count = int(row["success_count"])
        latencies = list(row["latencies"])
        p50 = _percentile(latencies, 0.5)
        p90 = _percentile(latencies, 0.9)
        success_rate = success_count / attempt_count if attempt_count else 0.0
        score = (success_rate * 1_000_000) - float(p50 or 999_999)
        scored.append({
            "provider": provider,
            "attempt_count": attempt_count,
            "success_count": success_count,
            "success_rate": round(success_rate, 4),
            "p50_latency_ms": p50,
            "p90_latency_ms": p90,
            "avg_success_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "errors": dict(row["errors"]),
            "artifact_count": int(row["artifact_count"]),
            "valid_image_count": int(row["valid_image_count"]),
            "invalid_image_count": int(row["invalid_image_count"]),
            "aspect_ratio_mismatch_count": int(row["aspect_ratio_mismatch_count"]),
            "ignored_fast_success_count": int(row["ignored_fast_success_count"]),
            "min_real_success_latency_ms": MIN_REAL_SUCCESS_LATENCY_MS,
            "window_seconds": window_seconds,
            "window_event_count": int(row["window_event_count"]),
            "score": round(score, 2),
        })

    return sorted(scored, key=lambda row: row["score"], reverse=True)


def _score_rows(
    rows: dict[str, dict[str, Any]],
    *,
    identity_key: str,
    window_seconds: float | None,
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for identity, row in rows.items():
        attempt_count = int(row["attempt_count"])
        success_count = int(row["success_count"])
        latencies = list(row["latencies"])
        p50 = _percentile(latencies, 0.5)
        p90 = _percentile(latencies, 0.9)
        success_rate = success_count / attempt_count if attempt_count else 0.0
        score = (success_rate * 1_000_000) - float(p50 or 999_999)
        scored.append({
            identity_key: identity,
            "attempt_count": attempt_count,
            "success_count": success_count,
            "success_rate": round(success_rate, 4),
            "p50_latency_ms": p50,
            "p90_latency_ms": p90,
            "avg_success_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else None,
            "errors": dict(row["errors"]),
            "artifact_count": int(row.get("artifact_count") or 0),
            "valid_image_count": int(row.get("valid_image_count") or 0),
            "invalid_image_count": int(row.get("invalid_image_count") or 0),
            "aspect_ratio_mismatch_count": int(row.get("aspect_ratio_mismatch_count") or 0),
            "ignored_fast_success_count": int(row.get("ignored_fast_success_count") or 0),
            "min_real_success_latency_ms": MIN_REAL_SUCCESS_LATENCY_MS,
            "window_seconds": window_seconds,
            "window_event_count": int(row["window_event_count"]),
            "score": round(score, 2),
        })
    return sorted(scored, key=lambda row: row["score"], reverse=True)


def pool_scores(
    events: list[dict[str, Any]],
    *,
    window_seconds: float | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    if window_seconds is not None and now is None:
        now = datetime.now(timezone.utc).timestamp()
    rows: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "attempt_count": 0,
        "success_count": 0,
        "latencies": [],
        "errors": Counter(),
        "artifact_count": 0,
        "valid_image_count": 0,
        "invalid_image_count": 0,
        "aspect_ratio_mismatch_count": 0,
        "window_event_count": 0,
        "provider_groups": Counter(),
        "providers": Counter(),
        "ignored_fast_success_count": 0,
    })

    for event in events:
        if event.get("event") != "batch_summary":
            continue
        if not _within_window(event, now=now, window_seconds=window_seconds):
            continue
        route_decisions = event.get("route_decisions") or {}
        if not isinstance(route_decisions, dict):
            continue
        for decision in route_decisions.values():
            if not isinstance(decision, dict):
                continue
            attempts = decision.get("attempts") or []
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, dict):
                    continue
                provider = str(attempt.get("provider") or "").strip()
                group = str(attempt.get("provider_group") or provider or "unknown-group").strip()
                pool_identity = str(attempt.get("pool_identity") or group).strip()
                if not pool_identity:
                    continue
                row = rows[pool_identity]
                row["window_event_count"] += 1
                row["attempt_count"] += 1
                if provider:
                    row["providers"][provider] += 1
                if group:
                    row["provider_groups"][group] += 1
                artifact_quality = _artifact_quality(attempt)
                row["artifact_count"] += int(artifact_quality["artifact_count"])
                row["valid_image_count"] += int(artifact_quality["valid_image_count"])
                row["invalid_image_count"] += int(artifact_quality["invalid_image_count"])
                row["aspect_ratio_mismatch_count"] += int(artifact_quality["aspect_ratio_mismatch_count"])
                ok = _effective_ok(attempt)
                if ok:
                    latency_ms = _real_success_latency_ms(attempt)
                    if latency_ms is None:
                        row["ignored_fast_success_count"] += 1
                        continue
                    row["success_count"] += 1
                    row["latencies"].append(latency_ms)
                else:
                    category = _failure_category(attempt)
                    if bool(attempt.get("ok")) and artifact_quality["known"] and not artifact_quality["valid_success"]:
                        category = "invalid_artifact"
                    row["errors"][category] += 1

    scored = _score_rows(rows, identity_key="pool_identity", window_seconds=window_seconds)
    for row in scored:
        source = rows[str(row["pool_identity"])]
        row["provider_groups"] = sorted(source["provider_groups"])
        row["providers"] = sorted(source["providers"])
    return scored


def pool_status(pool_score: dict[str, Any]) -> str:
    attempts = int(pool_score.get("attempt_count") or 0)
    success_count = int(pool_score.get("success_count") or 0)
    success_rate = float(pool_score.get("success_rate") or 0.0)
    if attempts <= 0:
        return "unproven"
    if success_count > 0 and success_rate >= 0.5:
        return "ready_recent_success"
    if success_count > 0:
        return "pool_risk_low_success_rate"
    return "pool_risk_no_recent_success"


def pool_status_counts(pool_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in pool_rows:
        status = str(row.get("status") or pool_status(row))
        counts[status] = counts.get(status, 0) + 1
    return counts


def provider_weight_recommendations(
    scores: list[dict[str, Any]],
    *,
    provider_order: list[str] | None = None,
    provider_pool_map: dict[str, str] | None = None,
    pools: list[dict[str, Any]] | None = None,
    latency_budget_ms: int | None = None,
    include_extra_scores: bool = False,
) -> list[dict[str, Any]]:
    scores_by_provider = {str(row.get("provider")): row for row in scores}
    scores_by_pool = {str(row.get("pool_identity")): row for row in (pools or [])}
    ordered = list(provider_order) if provider_order else [str(row.get("provider")) for row in scores]
    if include_extra_scores:
        for row in scores:
            provider = str(row.get("provider"))
            if provider and provider not in ordered:
                ordered.append(provider)

    rows: list[dict[str, Any]] = []
    known_success = [
        name
        for name in ordered
        if int((scores_by_provider.get(name) or {}).get("success_count") or 0) > 0
    ]
    unknown = [name for name in ordered if name not in scores_by_provider]
    known_failed = [
        name
        for name in ordered
        if name in scores_by_provider and name not in known_success
    ]

    for provider in known_success + unknown + known_failed:
        score = scores_by_provider.get(provider)
        pool_identity = (provider_pool_map or {}).get(provider)
        pool_score = scores_by_pool.get(str(pool_identity)) if pool_identity else None
        weight = 1
        reason = "unknown_or_stale_kept_for_recovery"
        if score:
            success_rate = float(score.get("success_rate") or 0.0)
            p50 = score.get("p50_latency_ms")
            budget_ms = latency_budget_ms if latency_budget_ms and latency_budget_ms > 0 else 30_000
            if success_rate >= 0.9 and isinstance(p50, int) and p50 <= budget_ms:
                weight = 3
                reason = "high_success_low_latency"
            elif success_rate >= 0.9 and isinstance(p50, int) and p50 > budget_ms:
                weight = 2
                reason = "high_success_over_latency_budget"
            elif success_rate >= 0.5:
                weight = 2
                reason = "usable_success_rate"
            elif int(score.get("attempt_count") or 0) > 0 and int(score.get("success_count") or 0) == 0:
                weight = 1
                reason = "recent_failures_retained_at_low_weight"
            else:
                reason = "low_confidence_retained_at_low_weight"
        if int((score or {}).get("success_count") or 0) == 0 and pool_score:
            pool_attempts = int(pool_score.get("attempt_count") or 0)
            pool_success_rate = float(pool_score.get("success_rate") or 0.0)
            if pool_attempts > 0 and pool_success_rate < 0.5:
                weight = 1
                reason = "pool_recent_failures_retained_at_low_weight"
        rows.append({
            "provider": provider,
            "pool_identity": pool_identity,
            "recommended_weight": weight,
            "reason": reason,
            "attempt_count": int((score or {}).get("attempt_count") or 0),
            "success_count": int((score or {}).get("success_count") or 0),
            "success_rate": (score or {}).get("success_rate"),
            "p50_latency_ms": (score or {}).get("p50_latency_ms"),
            "latency_budget_ms": latency_budget_ms,
            "ignored_fast_success_count": int((score or {}).get("ignored_fast_success_count") or 0),
            "artifact_count": int((score or {}).get("artifact_count") or 0),
            "valid_image_count": int((score or {}).get("valid_image_count") or 0),
            "invalid_image_count": int((score or {}).get("invalid_image_count") or 0),
            "aspect_ratio_mismatch_count": int((score or {}).get("aspect_ratio_mismatch_count") or 0),
            "pool_attempt_count": int((pool_score or {}).get("attempt_count") or 0),
            "pool_success_count": int((pool_score or {}).get("success_count") or 0),
            "pool_success_rate": (pool_score or {}).get("success_rate"),
            "pool_artifact_count": int((pool_score or {}).get("artifact_count") or 0),
            "pool_valid_image_count": int((pool_score or {}).get("valid_image_count") or 0),
            "pool_invalid_image_count": int((pool_score or {}).get("invalid_image_count") or 0),
            "pool_aspect_ratio_mismatch_count": int((pool_score or {}).get("aspect_ratio_mismatch_count") or 0),
        })
    return rows


def weighted_provider_slots(
    scores: list[dict[str, Any]],
    *,
    provider_order: list[str] | None = None,
    provider_pool_map: dict[str, str] | None = None,
    pools: list[dict[str, Any]] | None = None,
    latency_budget_ms: int | None = None,
    include_extra_scores: bool = False,
) -> list[str]:
    slots: list[str] = []
    for row in provider_weight_recommendations(
        scores,
        provider_order=provider_order,
        provider_pool_map=provider_pool_map,
        pools=pools,
        latency_budget_ms=latency_budget_ms,
        include_extra_scores=include_extra_scores,
    ):
        slots.extend([str(row["provider"])] * int(row["recommended_weight"]))
    return slots


def _failure_category(payload: dict[str, Any]) -> str:
    category = str(payload.get("error_category") or "").strip()
    if category and category != "None":
        return category
    error_code = str(payload.get("error_code") or "").strip()
    if error_code and error_code != "None":
        return error_code
    message = str(payload.get("error_message") or "").lower()
    if "401" in message or "unauthorized" in message:
        return "auth"
    if "429" in message or "too many requests" in message or "quota" in message:
        return "rate_limit"
    if "timeout" in message or "timed out" in message:
        return "timeout"
    return "unknown"


def pool_correlation_signals(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find weak evidence that supposedly independent lanes may share a limit.

    The router defaults to treating websites/account groups/lanes as independent.
    These signals do not override routing by themselves; they are operator-facing
    evidence for checking whether several lanes share quota, risk control, or a
    hidden upstream queue.
    """
    counters: dict[tuple[str, str, str, str], Counter[str]] = defaultdict(Counter)

    for event in events:
        if event.get("event") != "batch_summary":
            continue
        route_decisions = event.get("route_decisions") or {}
        if not isinstance(route_decisions, dict):
            continue
        for item_id, decision in route_decisions.items():
            if not isinstance(decision, dict):
                continue
            attempts = decision.get("attempts") or []
            if not isinstance(attempts, list):
                continue
            for attempt in attempts:
                if not isinstance(attempt, dict) or _effective_ok(attempt):
                    continue
                provider = str(attempt.get("provider") or "").strip()
                if not provider:
                    continue
                group = str(attempt.get("provider_group") or "unknown-group").strip()
                pool_identity = str(attempt.get("pool_identity") or group).strip()
                category = _failure_category(attempt)
                artifact_quality = _artifact_quality(attempt)
                if bool(attempt.get("ok")) and artifact_quality["known"] and not artifact_quality["valid_success"]:
                    category = "invalid_artifact"
                counters[(str(item_id), group, pool_identity, category)][provider] += 1

    signals: list[dict[str, Any]] = []
    for (item_id, group, pool_identity, category), providers in counters.items():
        if len(providers) < 2:
            continue
        signals.append({
            "item_id": item_id,
            "provider_group": group,
            "pool_identity": pool_identity,
            "failure_category": category,
            "providers": sorted(providers),
            "failure_count": int(sum(providers.values())),
            "reason": "multiple_lanes_in_same_pool_failed_same_item_with_same_category",
            "interpretation": "check_shared_quota_account_group_ip_risk_or_upstream_queue_before_treating_as_independent",
        })

    return sorted(
        signals,
        key=lambda row: (-int(row["failure_count"]), str(row["provider_group"]), str(row["item_id"])),
    )
