from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from router.core.redaction import redact_text, redact_value


REMEDIATION_EVENT_SCHEMA_VERSION = "remediation-event/v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _lower_text(*values: Any) -> str:
    return " ".join(_text(value).lower() for value in values if value is not None)


def _artifact_aspect_mismatch(row: dict[str, Any]) -> int:
    metrics = row.get("artifact_metrics") or {}
    if not isinstance(metrics, dict):
        return 0
    return int(metrics.get("aspect_ratio_mismatch_count") or 0)


def _event_id(seed: dict[str, Any]) -> str:
    stable = {
        "provider": seed.get("provider"),
        "pool_identity": seed.get("pool_identity"),
        "error_category": seed.get("error_category"),
        "recommended_action": seed.get("recommended_action"),
        "source": seed.get("source"),
    }
    digest = hashlib.sha1(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"REM-{digest[:12]}"


def _classification(category: str, message: str, hint: str) -> dict[str, Any]:
    combined = f"{category} {message} {hint}".lower()
    if any(marker in combined for marker in (
        "no available channels for model",
        "no available channel for model",
        "no available channels",
        "model unavailable",
        "channel unavailable",
    )):
        return {
            "event_type": "model_or_channel_unavailable",
            "severity": "medium",
            "recommended_action": "change_model_alias_account_or_channel",
            "safety_class": "local_config",
            "approval_required": True,
            "auto_execute": False,
        }
    if category in {"auth", "missing_credential"}:
        return {
            "event_type": "credential",
            "severity": "high",
            "recommended_action": "fix_or_rotate_credential",
            "safety_class": "secret_or_account",
            "approval_required": True,
            "auto_execute": False,
        }
    if category == "rate_limit":
        if any(marker in combined for marker in ("quota", "balance", "insufficient")):
            return {
                "event_type": "quota_or_balance",
                "severity": "high",
                "recommended_action": "operator_check_quota_or_balance",
                "safety_class": "external_spend",
                "approval_required": True,
                "auto_execute": False,
            }
        return {
            "event_type": "rate_limit",
            "severity": "medium",
            "recommended_action": "cooldown_or_reduce_concurrency",
            "safety_class": "local_safe",
            "approval_required": False,
            "auto_execute": True,
        }
    if category == "forbidden":
        return {
            "event_type": "route_or_risk_control",
            "severity": "high",
            "recommended_action": "inspect_route_account_or_risk_control",
            "safety_class": "external_account",
            "approval_required": True,
            "auto_execute": False,
        }
    if category == "unsupported":
        return {
            "event_type": "model_or_route_config",
            "severity": "medium",
            "recommended_action": "change_model_provider_or_route_capability",
            "safety_class": "local_config",
            "approval_required": True,
            "auto_execute": False,
        }
    if category in {"async_pending", "parse_error", "no_image", "invalid_artifact", "aspect_ratio_mismatch"}:
        return {
            "event_type": "adapter_drift",
            "severity": "medium",
            "recommended_action": "inspect_adapter_response_contract_or_artifact_quality",
            "safety_class": "adapter_contract",
            "approval_required": False,
            "auto_execute": False,
        }
    if category in {"timeout", "upstream_5xx", "connection"}:
        return {
            "event_type": "provider_outage",
            "severity": "medium",
            "recommended_action": "retry_fallback_or_cooldown_provider",
            "safety_class": "local_safe",
            "approval_required": False,
            "auto_execute": True,
        }
    return {
        "event_type": "transient_runtime",
        "severity": "low",
        "recommended_action": "collect_evidence_retry_or_fallback",
        "safety_class": "local_safe",
        "approval_required": False,
        "auto_execute": True,
    }


def _legacy_recovery_aliases(category: str, classification: dict[str, Any]) -> dict[str, Any]:
    action = str(classification["recommended_action"])
    risk = str(classification["safety_class"])
    if category == "rate_limit" and action == "operator_check_quota_or_balance":
        action = "operator_check_quota"
        risk = "external_account"
    elif category in {"auth", "missing_credential"}:
        action = "rotate_or_fix_key"
    elif category in {"timeout", "upstream_5xx", "connection"}:
        action = "passive_retry_or_fallback"
    elif category == "async_pending":
        action = "implement_or_enable_polling_adapter"
    elif category == "invalid_artifact":
        action = "inspect_response_blob_parser_or_provider_quality"
        risk = "output_quality"
    elif category == "aspect_ratio_mismatch":
        action = "review_provider_size_aspect_contract"
        risk = "output_quality"
    elif classification["recommended_action"] == "change_model_alias_account_or_channel":
        action = "change_model_alias_account_or_channel"
        risk = "local_config"
    return {
        "action": action,
        "risk": risk,
    }


def remediation_event_from_attempt(
    row: dict[str, Any],
    *,
    source: str = "attempt",
    now: float | None = None,
) -> dict[str, Any] | None:
    category = _text(row.get("error_category") or row.get("reason") or row.get("status") or "unknown")
    aspect_ratio_mismatch_count = _artifact_aspect_mismatch(row)
    if bool(row.get("ok")) and aspect_ratio_mismatch_count <= 0:
        return None
    if bool(row.get("ok")) and aspect_ratio_mismatch_count > 0:
        category = "aspect_ratio_mismatch"

    message = redact_text(_text(row.get("error_message"))) or ""
    remediation_hint = redact_text(_text(row.get("remediation_hint"))) or None
    classification = _classification(category, message, remediation_hint or "")
    legacy_aliases = _legacy_recovery_aliases(category, classification)
    event = {
        "schema_version": REMEDIATION_EVENT_SCHEMA_VERSION,
        "event_id": "",
        "event_type": classification["event_type"],
        "severity": classification["severity"],
        "provider": row.get("provider") or row.get("id"),
        "provider_group": row.get("provider_group"),
        "pool_identity": row.get("pool_identity") or row.get("provider_group"),
        "station_id": row.get("station_id"),
        "account_id": row.get("account_id"),
        "account_group": row.get("account_group"),
        "credential_id": row.get("credential_id"),
        "route_id": row.get("route_id"),
        "model": row.get("model"),
        "error_category": category,
        "remediation_hint": remediation_hint,
        "recommended_action": classification["recommended_action"],
        **legacy_aliases,
        "safety_class": classification["safety_class"],
        "approval_required": bool(classification["approval_required"]),
        "auto_execute": bool(classification["auto_execute"]),
        "evidence": redact_value({
            "source": source,
            "reason": row.get("reason"),
            "routing_action": row.get("routing_action"),
            "cooldown_until": row.get("cooldown_until"),
            "cooldown_seconds": row.get("cooldown_seconds"),
            "artifact_metrics": row.get("artifact_metrics"),
            "provider_response_contract": row.get("provider_response_contract"),
            "error_code": row.get("error_code"),
            "error_message": message,
            "aspect_ratio_mismatch_count": aspect_ratio_mismatch_count,
        }),
        "created_at": now if now is not None else time.time(),
        "source": source,
        "aspect_ratio_mismatch_count": aspect_ratio_mismatch_count,
        "secret_values_included": False,
    }
    event["event_id"] = _event_id(event)
    return {key: value for key, value in event.items() if value is not None}


def remediation_event_from_readiness_lane(row: dict[str, Any], *, now: float | None = None) -> dict[str, Any] | None:
    status = _text(row.get("status"))
    health = row.get("health") if isinstance(row.get("health"), dict) else {}
    if status in {"ready_recent_success", "configured_unproven", "needs_real_success"} and not health.get("cooldown_active"):
        return None
    synthetic = {
        **row,
        "provider": row.get("id") or row.get("provider"),
        "error_category": (
            "missing_credential"
            if status == "missing_credential"
            else (health.get("last_error_category") or health.get("cooldown_reason") or status or "unknown")
        ),
        "reason": status,
        "error_message": health.get("last_error_message"),
        "remediation_hint": health.get("cooldown_reason"),
        "cooldown_until": health.get("cooldown_until"),
    }
    return remediation_event_from_attempt(synthetic, source="readiness", now=now)


def remediation_events_from_items(items: list[dict[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        rows = list(item.get("attempts") or [])
        rows.extend(item.get("skipped_providers") or [])
        for row in rows:
            event = remediation_event_from_attempt(row, now=now)
            if event is None:
                continue
            event_id = str(event["event_id"])
            if event_id in seen:
                continue
            seen.add(event_id)
            events.append(event)
    return events


def remediation_events_from_readiness(lanes: list[dict[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in lanes:
        event = remediation_event_from_readiness_lane(row, now=now)
        if event is None:
            continue
        event_id = str(event["event_id"])
        if event_id in seen:
            continue
        seen.add(event_id)
        events.append(event)
    return events


def remediation_summary(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_action: dict[str, int] = {}
    by_safety_class: dict[str, int] = {}
    approval_required_count = 0
    auto_executable_count = 0
    for event in events:
        event_type = str(event.get("event_type") or "unknown")
        severity = str(event.get("severity") or "unknown")
        action = str(event.get("recommended_action") or "unknown")
        safety_class = str(event.get("safety_class") or "unknown")
        by_type[event_type] = by_type.get(event_type, 0) + 1
        by_severity[severity] = by_severity.get(severity, 0) + 1
        by_action[action] = by_action.get(action, 0) + 1
        by_safety_class[safety_class] = by_safety_class.get(safety_class, 0) + 1
        if event.get("approval_required"):
            approval_required_count += 1
        if event.get("auto_execute"):
            auto_executable_count += 1
    return {
        "event_count": len(events),
        "approval_required_count": approval_required_count,
        "auto_executable_count": auto_executable_count,
        "by_event_type": by_type,
        "by_severity": by_severity,
        "by_recommended_action": by_action,
        "by_safety_class": by_safety_class,
    }
