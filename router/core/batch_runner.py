from __future__ import annotations

import json
import os
import threading
import time
import uuid
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from router.core.artifacts import write_result
from router.core.batch_metrics import summarize_batch_attempts
from router.core.dedupe import request_hash
from router.core.health import (
    ProviderErrorCategory,
    RoutingAction,
    classify_generation_failure,
    cooldown_seconds_for,
    should_fallback_provider,
    should_retry_same_provider,
)
from router.core.ledger import write_batch_ledger
from router.core.models import GenerationRequest, GenerationResult, ProviderCapability
from router.core.perf_log import append_batch_summary
from router.core.perf_score import pool_scores, provider_scores, weighted_provider_slots
from router.core.provider_contracts import check_provider_capability
from router.core.provider_locks import ProviderLease, ProviderLeaseManager
from router.core.provider_registry import (
    build_adapter,
    ordered_lane_ids,
    provider_capability_registry,
    provider_group_for,
    provider_pool_metadata_for,
    provider_tier_for,
)
from router.core.provider_state_store import ProviderHealthStore
from router.core.probe_policy import probe_decision
from router.core.recovery import recovery_events_from_items
from router.core.remediation_events import remediation_events_from_items, remediation_summary
from router.core.redaction import redact_text
from router.core.request_metrics import reference_image_metrics
from router.core.response_contract import response_contract_signal
from router.core.routing_policy import RoutingPolicyPlan, resolve_routing_policy
from router.core.routing_presets import RoutingPreset
from router.core.scheduler import ProviderState
from router.core.simage_selection import select_simage_model


KNOWN_PROVIDERS = [
    "aiwave",
    "laozhang",
    "apiyi",
    "gptge",
    "aifast",
    "vectorengine_compat",
    "vectorengine",
    "aifast_compat",
]

PROVIDER_GROUPS = {
    "vectorengine": "vectorengine-group",
    "vectorengine_compat": "vectorengine-group",
    "aifast": "aifast-group",
    "aifast_compat": "aifast-group",
    "aiwave": "aiwave-group",
    "gptge": "gptge-group",
    "laozhang": "laozhang-group",
    "apiyi": "apiyi-group",
}


def _provider_group(name: str) -> str:
    return provider_group_for(name)


def _provider_tier(name: str) -> str:
    return provider_tier_for(name)


def _provider_pool_metadata(name: str) -> dict[str, Any]:
    return provider_pool_metadata_for(name)


@dataclass(slots=True)
class BatchItemResult:
    item_id: str
    request_hash: str
    request_type: str
    status: str
    request_contract: dict[str, Any] = field(default_factory=dict)
    selected_provider: str | None = None
    selected_provider_group: str | None = None
    selected_provider_pool_identity: str | None = None
    attempted_providers: list[str] = field(default_factory=list)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    race_requested_providers: list[str] = field(default_factory=list)
    race_launched_providers: list[str] = field(default_factory=list)
    race_completed_providers: list[str] = field(default_factory=list)
    skipped_providers: list[dict[str, Any]] = field(default_factory=list)
    routing_policy: str | None = None
    effective_routing_mode: str | None = None
    planned_providers: list[str] = field(default_factory=list)
    policy_warnings: list[str] = field(default_factory=list)
    policy_plan: dict[str, Any] = field(default_factory=dict)
    capability_decisions: list[dict[str, Any]] = field(default_factory=list)
    probe_decisions: list[dict[str, Any]] = field(default_factory=list)
    artifact_paths: list[str] = field(default_factory=list)
    raw_response_path: str | None = None
    latency_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    replacement_for_item_id: str | None = None
    replacement_reason: str | None = None
    replacement_created_at: float | None = None
    replacement_started_at: float | None = None
    replacement_provider_plan: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BatchRunResult:
    job_type: str
    item_results: list[BatchItemResult]
    summary_path: str
    ledger_path: str
    success_count: int
    failure_count: int
    dry_run: bool = False


def _now_stamp() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _read_json(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _first(mapping: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return default


def _resolve_ref(path: str, base_dir: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        p = (base_dir / p).resolve()
    return str(p)


def _request_from_payload(
    payload: dict[str, Any],
    *,
    base_dir: Path,
    job_type: str,
    default_profile: str,
    default_source: str,
    default_routing_policy: str | None = None,
    default_provider_tier: str | None = None,
) -> GenerationRequest:
    request_type = _first(payload, "requestType", "request_type")
    if request_type not in {"text_to_image", "image_to_image"}:
        raise ValueError(f"Unsupported requestType: {request_type!r}")

    refs = [
        _resolve_ref(p, base_dir)
        for p in _first(payload, "referenceImages", "reference_images", default=[])
    ]

    prompt = _first(payload, "prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Each request must provide a non-empty prompt.")

    metadata = dict(_first(payload, "metadata", default={}) or {})
    metadata.setdefault("source", default_source)
    metadata.setdefault("referenceMetrics", reference_image_metrics(refs))
    routing_policy = _first(payload, "routingPolicy", "routing_policy", default=default_routing_policy)
    if routing_policy:
        metadata["routingPolicy"] = routing_policy
    provider_tier = _first(payload, "providerTier", "provider_tier", "sourceTier", "source_tier", default=default_provider_tier)
    if provider_tier:
        metadata["providerTier"] = provider_tier

    return GenerationRequest(
        job_type=job_type,  # type: ignore[arg-type]
        request_type=request_type,  # type: ignore[arg-type]
        profile=_first(payload, "profile", default=default_profile),
        prompt=prompt,
        negative_prompt=_first(payload, "negativePrompt", "negative_prompt", default=""),
        reference_images=refs,
        count=int(_first(payload, "count", default=1) or 1),
        aspect_ratio=_first(payload, "aspectRatio", "aspect_ratio"),
        size=_first(payload, "size"),
        quality=_first(payload, "quality", default="standard"),
        allow_fallback=bool(_first(payload, "allowFallback", "allow_fallback", default=True)),
        dedupe_policy=_first(payload, "dedupePolicy", "dedupe_policy", default="skip-identical"),
        routing_mode=_first(payload, "routingMode", "routing_mode", default="fallback"),
        race_providers=list(_first(payload, "raceProviders", "race_providers", default=[]) or []),
        metadata=metadata,
    )



def load_job_items(path: str, default_profile: str = "generic") -> tuple[str, list[tuple[str, GenerationRequest, list[str] | None]]]:
    return load_job_items_with_defaults(path, default_profile=default_profile)


def load_job_items_with_defaults(
    path: str,
    *,
    default_profile: str = "generic",
    default_routing_policy: str | None = None,
    default_provider_tier: str | None = None,
) -> tuple[str, list[tuple[str, GenerationRequest, list[str] | None]]]:
    job = _read_json(path)
    base_dir = Path(path).resolve().parent
    job_type = _first(job, "jobType", "job_type")
    if job_type not in {"manifest", "variants"}:
        raise ValueError(f"Unsupported jobType: {job_type!r}")

    items: list[tuple[str, GenerationRequest, list[str] | None]] = []
    default_source = str(Path(path).resolve())
    job_metadata = dict(_first(job, "metadata", default={}) or {})

    if job_type == "manifest":
        raw_items = _first(job, "items", default=[])
        if not isinstance(raw_items, list) or not raw_items:
            raise ValueError("Manifest job must contain a non-empty items array.")
        for idx, item in enumerate(raw_items, start=1):
            if not isinstance(item, dict):
                raise ValueError("Each manifest item must be an object.")
            item_id = _first(item, "id", default=f"item-{idx:03d}")
            item_payload = {**item}
            if job_metadata:
                item_payload["metadata"] = {**job_metadata, **dict(_first(item, "metadata", default={}) or {})}
            request = _request_from_payload(
                item_payload,
                base_dir=base_dir,
                job_type="manifest",
                default_profile=_first(job, "profile", default=default_profile),
                default_source=default_source,
                default_routing_policy=_first(
                    job,
                    "routingPolicy",
                    "routing_policy",
                    default=default_routing_policy,
                ),
                default_provider_tier=_first(
                    job,
                    "providerTier",
                    "provider_tier",
                    "sourceTier",
                    "source_tier",
                    default=default_provider_tier,
                ),
            )
            providers = _first(item, "providers", "providerPreference")
            items.append((item_id, request, providers))
        return job_type, items

    variants = int(_first(job, "variants", "count", default=1) or 1)
    if variants < 1:
        raise ValueError("Variants job must request at least 1 variant.")
    base_request = _request_from_payload(
        job,
        base_dir=base_dir,
        job_type="variants",
        default_profile=_first(job, "profile", default=default_profile),
        default_source=default_source,
        default_routing_policy=_first(
            job,
            "routingPolicy",
            "routing_policy",
            default=default_routing_policy,
        ),
        default_provider_tier=_first(
            job,
            "providerTier",
            "provider_tier",
            "sourceTier",
            "source_tier",
            default=default_provider_tier,
        ),
    )
    providers = _first(job, "providers", "providerPreference")
    for idx in range(1, variants + 1):
        req = GenerationRequest(
            job_type=base_request.job_type,
            request_type=base_request.request_type,
            profile=base_request.profile,
            prompt=base_request.prompt,
            negative_prompt=base_request.negative_prompt,
            reference_images=list(base_request.reference_images),
            count=1,
            aspect_ratio=base_request.aspect_ratio,
            size=base_request.size,
            quality=base_request.quality,
            allow_fallback=base_request.allow_fallback,
            dedupe_policy=base_request.dedupe_policy,
            routing_mode=base_request.routing_mode,
            race_providers=list(base_request.race_providers),
            metadata={**base_request.metadata, "variant_index": idx, "variant_total": variants},
        )

        items.append((f"variant-{idx:03d}", req, providers))
    return job_type, items


def _default_provider_order(request: GenerationRequest) -> list[str]:
    lane_order = ordered_lane_ids()
    if lane_order:
        return lane_order
    ref_count = len(request.reference_images)
    if request.request_type == "text_to_image":
        return ["aiwave", "laozhang", "apiyi", "gptge", "aifast", "vectorengine_compat", "vectorengine"]
    if ref_count > 1:
        # Prefer true multi-ref lanes first, but allow single-ref lanes to participate
        # via request downgrade in _materialize_request_for_provider().
        return ["vectorengine_compat", "vectorengine", "aifast", "aiwave"]
    return ["vectorengine_compat", "vectorengine", "aifast", "aiwave"]


def _normalize_provider_tier(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    if raw in {"normal", "ordinary", "daily", "default", "regular", "common", "普通", "普通组", "日常", "日常通道"}:
        return "daily"
    if raw in {"professional", "pro", "premium", "expert", "专业", "专业组", "专业级"}:
        return "professional"
    return raw


def _requested_provider_tier(request: GenerationRequest, explicit_preference: list[str] | None) -> str | None:
    if explicit_preference:
        return None
    return _normalize_provider_tier(
        request.metadata.get("providerTier")
        or request.metadata.get("provider_tier")
        or request.metadata.get("sourceTier")
        or request.metadata.get("source_tier")
        or "professional"
    )


def _filter_order_by_provider_tier(order: list[str], request: GenerationRequest, explicit_preference: list[str] | None) -> list[str]:
    tier = _requested_provider_tier(request, explicit_preference)
    if not tier:
        return order
    tier_order = [name for name in order if _provider_tier(name) == tier]
    return tier_order


def _materialize_request_for_provider(request: GenerationRequest, cap: ProviderCapability) -> GenerationRequest:
    ref_count = len(request.reference_images)
    if request.request_type != "image_to_image":
        return request
    if ref_count <= cap.max_reference_images or cap.max_reference_images <= 0:
        return request

    if request.metadata.get("requireFullReferenceLock"):
        raise ValueError(
            f"Provider {cap.provider} supports max_reference_images={cap.max_reference_images}, "
            f"but request requires full reference lock with {ref_count} references."
        )

    kept_refs = list(request.reference_images[: cap.max_reference_images])
    metadata = {
        **request.metadata,
        "referenceDowngraded": True,
        "referenceOriginalCount": ref_count,
        "referenceUsedCount": len(kept_refs),
        "referenceDowngradeProvider": cap.provider,
        "referenceOriginalMetrics": reference_image_metrics(request.reference_images),
        "referenceUsedMetrics": reference_image_metrics(kept_refs),
        "referenceMetrics": reference_image_metrics(kept_refs),
    }
    return GenerationRequest(
        job_type=request.job_type,
        request_type=request.request_type,
        profile=request.profile,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        reference_images=kept_refs,
        count=request.count,
        aspect_ratio=request.aspect_ratio,
        size=request.size,
        quality=request.quality,
        allow_fallback=request.allow_fallback,
        dedupe_policy=request.dedupe_policy,
        routing_mode=request.routing_mode,
        race_providers=list(request.race_providers),
        metadata=metadata,
    )


def _with_request_metadata(request: GenerationRequest, metadata: dict[str, Any]) -> GenerationRequest:
    return GenerationRequest(
        job_type=request.job_type,
        request_type=request.request_type,
        profile=request.profile,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        reference_images=list(request.reference_images),
        count=request.count,
        aspect_ratio=request.aspect_ratio,
        size=request.size,
        quality=request.quality,
        allow_fallback=request.allow_fallback,
        dedupe_policy=request.dedupe_policy,
        routing_mode=request.routing_mode,
        race_providers=list(request.race_providers),
        metadata={**request.metadata, **metadata},
    )


def _with_request_defaults(request: GenerationRequest, *, size: str | None = None) -> GenerationRequest:
    resolved_size = request.size or size
    if resolved_size == request.size:
        return request
    return GenerationRequest(
        job_type=request.job_type,
        request_type=request.request_type,
        profile=request.profile,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        reference_images=list(request.reference_images),
        count=request.count,
        aspect_ratio=request.aspect_ratio,
        size=resolved_size,
        quality=request.quality,
        allow_fallback=request.allow_fallback,
        dedupe_policy=request.dedupe_policy,
        routing_mode=request.routing_mode,
        race_providers=list(request.race_providers),
        metadata=dict(request.metadata),
    )


def _clone_request(request: GenerationRequest, metadata: dict[str, Any] | None = None) -> GenerationRequest:
    return GenerationRequest(
        job_type=request.job_type,
        request_type=request.request_type,
        profile=request.profile,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        reference_images=list(request.reference_images),
        count=request.count,
        aspect_ratio=request.aspect_ratio,
        size=request.size,
        quality=request.quality,
        allow_fallback=request.allow_fallback,
        dedupe_policy=request.dedupe_policy,
        routing_mode=request.routing_mode,
        race_providers=list(request.race_providers),
        metadata=dict(metadata if metadata is not None else request.metadata),
    )


def _request_replacement_metadata(request: GenerationRequest) -> dict[str, Any]:
    return dict(request.metadata)



def _provider_order(request: GenerationRequest, provider_preference: list[str] | None) -> list[str]:
    preferred = list(provider_preference or [])
    restricted = request.metadata.get("providerRestriction")
    if restricted is not None:
        return [name for name in restricted if name in preferred or not preferred]
    default_order = _default_provider_order(request)
    ordered: list[str] = []
    for name in preferred + default_order:
        if name not in ordered:
            ordered.append(name)
    return _filter_order_by_provider_tier(ordered, request, preferred or None)


def _request_policy_name(request: GenerationRequest, override_policy: str | None) -> str | None:
    return override_policy or request.metadata.get("routingPolicy") or request.metadata.get("routing_policy")


def _spread_provider_order(provider_order: list[str], item_index: int) -> list[str]:
    if not provider_order:
        return []
    offset = item_index % len(provider_order)
    return provider_order[offset:] + provider_order[:offset]


def _read_perf_events(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    source = Path(path)
    if not source.exists():
        return []
    files = sorted(source.glob("*.jsonl")) if source.is_dir() else [source]
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


def _provider_pool_map(provider_order: list[str]) -> dict[str, str]:
    return {
        provider: str(_provider_pool_metadata(provider)["pool_identity"])
        for provider in provider_order
    }


def _weighted_slots(
    provider_order: list[str],
    scores_by_provider: dict[str, dict[str, Any]],
    scores_by_pool: dict[str, dict[str, Any]] | None = None,
    latency_budget_ms: int | None = None,
) -> list[str]:
    if not provider_order:
        return []
    return weighted_provider_slots(
        list(scores_by_provider.values()),
        provider_order=provider_order,
        provider_pool_map=_provider_pool_map(provider_order),
        pools=list((scores_by_pool or {}).values()),
        latency_budget_ms=latency_budget_ms,
    )


def _weighted_provider_order(
    provider_order: list[str],
    item_index: int,
    scores_by_provider: dict[str, dict[str, Any]],
    scores_by_pool: dict[str, dict[str, Any]] | None = None,
    latency_budget_ms: int | None = None,
) -> list[str]:
    slots = _weighted_slots(provider_order, scores_by_provider, scores_by_pool, latency_budget_ms)
    if not slots:
        return provider_order
    chosen = slots[item_index % len(slots)]
    return [chosen] + [name for name in provider_order if name != chosen]


def _perf_window_seconds() -> float | None:
    raw = os.environ.get("MIR_WEIGHTED_PERF_WINDOW_SECONDS", "86400")
    try:
        value = float(raw)
    except ValueError:
        return 86400.0
    return value if value > 0 else None


def _spring_back_recovering(state: ProviderState, now: float, window_seconds: int | None) -> bool:
    if not window_seconds or window_seconds <= 0:
        return False
    if state.cooldown_until <= 0 or state.cooldown_until > now:
        return False
    if now > state.cooldown_until + window_seconds:
        return False
    if state.last_failure_at is None:
        return False
    if state.last_success_at is not None and state.last_success_at >= state.last_failure_at:
        return False
    return True


def _apply_spring_back_order(
    order: list[str],
    states: dict[str, ProviderState],
    preset: RoutingPreset | None,
    item_index: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    if not preset or not order or len(order) < 2:
        return order, []
    window_seconds = preset.spring_back_seconds
    probe_every = preset.spring_back_probe_every or 1
    if not window_seconds or probe_every <= 1:
        return order, []

    primary = order[0]
    state = states.get(primary)
    now = time.time()
    if state is None or not _spring_back_recovering(state, now, window_seconds):
        return order, []

    if item_index % probe_every == 0:
        return order, []

    reordered = order[1:] + [primary]
    skipped = [{
        "provider": primary,
        "reason": "spring_back_deferred",
        **_provider_pool_metadata(primary),
        "cooldown_until": state.cooldown_until,
        "cooldown_reason": state.cooldown_reason,
        "last_error_category": state.last_error_category,
        "spring_back_seconds": window_seconds,
        "spring_back_probe_every": probe_every,
        "spring_back_next_probe_modulo": 0,
    }]
    return reordered, skipped


def _normalized_policy_name(value: str | None) -> str:
    return (value or "").strip().lower().replace("-", "_")


def _apply_policy_to_request(request: GenerationRequest, plan: RoutingPolicyPlan) -> GenerationRequest:
    metadata = {**request.metadata, "routingPolicy": plan.policy}
    return GenerationRequest(
        job_type=request.job_type,
        request_type=request.request_type,
        profile=request.profile,
        prompt=request.prompt,
        negative_prompt=request.negative_prompt,
        reference_images=list(request.reference_images),
        count=request.count,
        aspect_ratio=request.aspect_ratio,
        size=request.size,
        quality=request.quality,
        allow_fallback=plan.allow_fallback,
        dedupe_policy=request.dedupe_policy,
        routing_mode=plan.routing_mode,  # type: ignore[arg-type]
        race_providers=list(plan.race_providers or request.race_providers),
        metadata=metadata,
    )


def _attach_policy_result(result: BatchItemResult, plan: RoutingPolicyPlan) -> BatchItemResult:
    result.routing_policy = plan.policy
    result.effective_routing_mode = plan.routing_mode
    result.planned_providers = list(plan.provider_order)
    result.policy_warnings = list(plan.warnings)
    result.policy_plan = plan.to_dict()
    return result


def _request_contract(request: GenerationRequest) -> dict[str, Any]:
    return {
        "request_type": request.request_type,
        "size": request.size,
        "aspect_ratio": request.aspect_ratio,
        "reference_count": len(request.reference_images),
        "source": request.metadata.get("source"),
        "model_selection": request.metadata.get("modelSelection"),
    }


def _attach_pre_policy_skips(result: BatchItemResult, skipped: list[dict[str, Any]]) -> BatchItemResult:
    if not skipped:
        return result
    existing = {(row.get("provider"), row.get("reason")) for row in result.skipped_providers}
    for row in skipped:
        key = (row.get("provider"), row.get("reason"))
        if key not in existing:
            result.skipped_providers.append(row)
    return result


def _build_adapters(candidate_providers: list[str] | None) -> tuple[dict[str, Any], dict[str, str]]:
    adapters: dict[str, Any] = {}
    errors: dict[str, str] = {}
    configured_lanes = ordered_lane_ids()
    names = candidate_providers or configured_lanes or KNOWN_PROVIDERS
    for name in names:
        try:
            adapters[name] = build_adapter(name)
        except Exception as exc:  # deliberate startup capture for batch summary
            errors[name] = f"{type(exc).__name__}: {exc}"
    return adapters, errors


def _supports_request(cap: ProviderCapability, request: GenerationRequest) -> bool:
    return check_provider_capability(cap, request).supported


def _should_failover(result: GenerationResult) -> bool:
    return should_fallback_provider(classify_generation_failure(result))


def _effective_result_ok(result: GenerationResult) -> bool:
    return classify_generation_failure(result).category == ProviderErrorCategory.SUCCESS


def _failure_result_code(result: GenerationResult | None, attempts: list[dict[str, Any]]) -> str:
    if result and result.error_code:
        return str(result.error_code)
    if attempts:
        category = attempts[-1].get("error_category")
        if category:
            return str(category)
    return "no_provider"


def _failure_result_message(result: GenerationResult | None, attempts: list[dict[str, Any]], fallback: str) -> str:
    if result and result.error_message:
        return str(result.error_message)
    if attempts:
        category = str(attempts[-1].get("error_category") or "unknown")
        hint = attempts[-1].get("remediation_hint")
        if hint:
            return f"{category}: {hint}"
        return category
    return fallback


def _reference_attempt_metrics(
    original_request: GenerationRequest,
    provider_request: GenerationRequest,
) -> dict[str, Any]:
    original = reference_image_metrics(original_request.reference_images)
    used = reference_image_metrics(provider_request.reference_images)
    return {
        "reference_original_count": original["count"],
        "reference_used_count": used["count"],
        "reference_downgraded": used["count"] != original["count"],
        "reference_original_total_bytes": original["total_bytes"],
        "reference_used_total_bytes": used["total_bytes"],
        "reference_original_estimated_base64_bytes": original["estimated_base64_bytes"],
        "reference_used_estimated_base64_bytes": used["estimated_base64_bytes"],
        "reference_missing_count": used["missing_count"],
    }


def _provider_response_contract(result: GenerationResult) -> dict[str, Any]:
    return response_contract_signal(result.provider_response)


def _comparison_contract_summary(
    provider: str,
    request: GenerationRequest,
    provider_request: GenerationRequest | None = None,
) -> dict[str, Any]:
    metadata = _provider_pool_metadata(provider)
    image_contract = metadata.get("image_contract")
    return {
        "production_line": metadata.get("production_line"),
        "provider": provider,
        "station": metadata.get("station_id"),
        "model_class": metadata.get("model_class"),
        "model": metadata.get("model"),
        "image_contract": image_contract if isinstance(image_contract, dict) else {},
        "reference_count": len((provider_request or request).reference_images),
        "full_lock": bool(request.metadata.get("requireFullReferenceLock")),
        "degradation_status": metadata.get("degradation_status") or "active",
    }


def _artifact_dimensions(metrics: dict[str, Any] | None) -> list[dict[str, int]]:
    if not isinstance(metrics, dict):
        return []
    images = metrics.get("images")
    if not isinstance(images, list):
        return []
    rows: list[dict[str, int]] = []
    for image in images:
        if not isinstance(image, dict):
            continue
        width = image.get("width")
        height = image.get("height")
        if isinstance(width, int) and isinstance(height, int):
            rows.append({
                "width": width,
                "height": height,
                "long_edge": max(width, height),
            })
    return rows


def _remote_task_id_from_result(result: GenerationResult) -> str | None:
    payload = result.provider_response
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("task_id"):
                return str(item["task_id"])
    if isinstance(data, dict) and data.get("task_id"):
        return str(data["task_id"])
    if payload.get("task_id"):
        return str(payload["task_id"])
    return None


def _cooldown_active(state: ProviderState, now: float | None = None) -> bool:
    return state.cooldown_until > (time.time() if now is None else now)


def _circuit_breaker_threshold() -> int:
    raw = os.environ.get("MIR_CIRCUIT_BREAKER_FAILURE_THRESHOLD", "3")
    try:
        value = int(raw)
    except ValueError:
        return 3
    return max(0, value)


def _circuit_breaker_cooldown_seconds() -> float:
    raw = os.environ.get("MIR_CIRCUIT_BREAKER_COOLDOWN_SECONDS", "300")
    try:
        value = float(raw)
    except ValueError:
        return 300.0
    return max(0.0, value)


def _circuit_breaker_reason(decision) -> str:
    return f"circuit_breaker_{decision.category.value}"


def _health_aware_failure_cooldown_seconds() -> float:
    raw = os.environ.get("MIR_HEALTH_AWARE_FAILURE_COOLDOWN_SECONDS", "180")
    try:
        value = float(raw)
    except ValueError:
        return 180.0
    return max(0.0, value)


def _health_aware_backup_after_seconds() -> float:
    raw = os.environ.get("MIR_HEALTH_AWARE_BACKUP_AFTER_SECONDS", "60")
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return max(0.0, value)


def _is_health_aware_request(request: GenerationRequest) -> bool:
    return _normalized_policy_name(
        str(request.metadata.get("routingPolicy") or request.metadata.get("routing_policy") or "")
    ) == "health_aware_load_balance"


def _health_aware_cooldown_category(category: str) -> bool:
    return category in {
        ProviderErrorCategory.CONNECTION.value,
        ProviderErrorCategory.TIMEOUT.value,
        ProviderErrorCategory.UPSTREAM_5XX.value,
        ProviderErrorCategory.RATE_LIMIT.value,
    }


def _record_skipped_provider(
    skipped_providers: list[dict[str, Any]],
    provider: str,
    *,
    reason: str,
    state: ProviderState | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if any(row.get("provider") == provider and row.get("reason") == reason for row in skipped_providers):
        return
    record: dict[str, Any] = {
        "provider": provider,
        "reason": reason,
        **_provider_pool_metadata(provider),
    }
    if state is not None:
        if state.cooldown_until:
            record["cooldown_until"] = state.cooldown_until
        if state.cooldown_reason:
            record["cooldown_reason"] = state.cooldown_reason
        if state.last_error_category:
            record["error_category"] = state.last_error_category
        if state.last_error_message:
            record["error_message"] = redact_text(state.last_error_message)
    if details:
        record.update(details)
    skipped_providers.append(record)


def _record_unsupported_provider(
    skipped_providers: list[dict[str, Any]],
    provider: str,
    cap: ProviderCapability,
    request: GenerationRequest,
    state: ProviderState | None,
) -> None:
    decision = check_provider_capability(cap, request)
    if decision.supported:
        return
    _record_skipped_provider(
        skipped_providers,
        provider,
        reason=decision.reason,
        state=state,
        details={
            "required_reference_count": decision.required_reference_count,
            "max_reference_images": decision.max_reference_images,
            "will_downgrade_references": decision.will_downgrade_references,
        },
    )


def _no_provider_error_code(skipped_providers: list[dict[str, Any]]) -> str:
    if any(row.get("reason") not in {"cooldown"} for row in skipped_providers):
        return "unsupported_capability"
    return "no_provider"


def _apply_provider_health_result(
    provider: str,
    result: GenerationResult,
    states: dict[str, ProviderState],
    lock: threading.Lock,
    *,
    health_aware: bool = False,
) -> tuple[str, str, float, float | None, str | None]:
    decision = classify_generation_failure(result)
    cooldown_seconds = cooldown_seconds_for(decision)
    cooldown_until: float | None = None
    routing_action = decision.action
    if (
        health_aware
        and decision.retryable
        and _health_aware_cooldown_category(decision.category.value)
        and cooldown_seconds <= 0
    ):
        cooldown_seconds = _health_aware_failure_cooldown_seconds()
        if cooldown_seconds > 0:
            cooldown_until = time.time() + cooldown_seconds
            routing_action = RoutingAction.COOLDOWN_PROVIDER
            with lock:
                state = states.get(provider)
                if state is not None:
                    ProviderHealthStore.apply_failure(
                        state,
                        category=decision.category.value,
                        message=result.error_message,
                        cooldown_until=cooldown_until,
                        cooldown_reason=f"health_aware_{decision.category.value}",
                    )
            return decision.category.value, routing_action.value, cooldown_seconds, cooldown_until, (
                decision.remediation_hint or f"health_aware_{decision.category.value}"
            )
    if decision.action == RoutingAction.COOLDOWN_PROVIDER and cooldown_seconds > 0:
        cooldown_until = time.time() + cooldown_seconds
        with lock:
            state = states.get(provider)
            if state is not None:
                ProviderHealthStore.apply_failure(
                    state,
                    category=decision.category.value,
                    message=result.error_message,
                    cooldown_until=cooldown_until,
                    cooldown_reason=decision.remediation_hint or decision.category.value,
                )
    elif decision.category == ProviderErrorCategory.SUCCESS:
        with lock:
            state = states.get(provider)
            if state is not None:
                ProviderHealthStore.apply_success(state, signal="passive_success")
    else:
        with lock:
            state = states.get(provider)
            if state is not None:
                ProviderHealthStore.apply_failure(
                    state,
                    category=decision.category.value,
                    message=result.error_message,
                )
                threshold = _circuit_breaker_threshold()
                breaker_cooldown = _circuit_breaker_cooldown_seconds()
                if (
                    threshold > 0
                    and breaker_cooldown > 0
                    and decision.retryable
                    and decision.action == RoutingAction.RETRY_SAME_PROVIDER
                    and state.consecutive_failures >= threshold
                ):
                    cooldown_seconds = breaker_cooldown
                    cooldown_until = time.time() + breaker_cooldown
                    routing_action = RoutingAction.COOLDOWN_PROVIDER
                    state.cooldown_until = max(state.cooldown_until, cooldown_until)
                    state.cooldown_reason = _circuit_breaker_reason(decision)
    return decision.category.value, routing_action.value, cooldown_seconds, cooldown_until, decision.remediation_hint


def _acquire_provider(
    request: GenerationRequest,
    remaining_order: list[str],
    registry: dict[str, ProviderCapability],
    states: dict[str, ProviderState],
    lock: threading.Lock,
    skipped_providers: list[dict[str, Any]] | None = None,
    lease_manager: ProviderLeaseManager | None = None,
    leases: dict[str, list[ProviderLease]] | None = None,
    task_id: str | None = None,
) -> str | None:
    while True:
        with lock:
            eligible: list[tuple[int, int, str]] = []
            remaining_existing = [name for name in remaining_order if name in registry and name in states]
            for order_idx, name in enumerate(remaining_existing):
                cap = registry[name]
                state = states[name]
                if not cap.enabled or not state.healthy:
                    continue
                if _cooldown_active(state):
                    if skipped_providers is not None:
                        _record_skipped_provider(skipped_providers, name, reason="cooldown", state=state)
                    continue
                if not _supports_request(cap, request):
                    if skipped_providers is not None:
                        _record_unsupported_provider(skipped_providers, name, cap, request, state)
                    continue
                if state.inflight >= cap.max_concurrency:
                    continue
                eligible.append((state.inflight, order_idx, name))
            if eligible:
                eligible.sort(key=lambda row: (row[0], row[1], row[2]))
                for _, _, chosen in eligible:
                    cap = registry[chosen]
                    lease = (
                        lease_manager.acquire(
                            chosen,
                            max_concurrency=cap.max_concurrency,
                            task_id=task_id,
                            wait_timeout_seconds=0.0,
                        )
                        if lease_manager is not None
                        else None
                    )
                    if lease_manager is not None and lease is None:
                        continue
                    states[chosen].inflight += 1
                    if lease is not None and leases is not None:
                        leases.setdefault(chosen, []).append(lease)
                    return chosen

            waitable = False
            for name in remaining_existing:
                cap = registry[name]
                state = states[name]
                if not cap.enabled or not state.healthy:
                    continue
                if _cooldown_active(state):
                    continue
                if _supports_request(cap, request):
                    waitable = True
                    break
            if not waitable:
                return None
        time.sleep(0.05)


def _release_provider(
    provider: str,
    states: dict[str, ProviderState],
    lock: threading.Lock,
    *,
    lease_manager: ProviderLeaseManager | None = None,
    leases: dict[str, list[ProviderLease]] | None = None,
) -> None:
    if lease_manager is not None and leases is not None:
        provider_leases = leases.get(provider) or []
        lease = provider_leases.pop() if provider_leases else None
        lease_manager.release(lease)
    with lock:
        if provider in states:
            states[provider].inflight = max(0, states[provider].inflight - 1)


def _reserve_race_providers(
    request: GenerationRequest,
    race_order: list[str],
    registry: dict[str, ProviderCapability],
    states: dict[str, ProviderState],
    lock: threading.Lock,
    skipped_providers: list[dict[str, Any]] | None = None,
    lease_manager: ProviderLeaseManager | None = None,
    leases: dict[str, list[ProviderLease]] | None = None,
    task_id: str | None = None,
) -> list[str]:
    while True:
        with lock:
            eligible_names: list[str] = []
            blocked = False
            for name in race_order:
                if name not in registry or name not in states:
                    continue
                cap = registry[name]
                state = states[name]
                if not cap.enabled or not state.healthy:
                    continue
                if _cooldown_active(state):
                    if skipped_providers is not None:
                        _record_skipped_provider(skipped_providers, name, reason="cooldown", state=state)
                    continue
                if not _supports_request(cap, request):
                    if skipped_providers is not None:
                        _record_unsupported_provider(skipped_providers, name, cap, request, state)
                    continue
                eligible_names.append(name)
                if state.inflight >= cap.max_concurrency:
                    blocked = True

            if not eligible_names:
                return []

            if not blocked:
                acquired_leases: list[tuple[str, ProviderLease]] = []
                if lease_manager is not None:
                    for name in eligible_names:
                        lease = lease_manager.acquire(
                            name,
                            max_concurrency=registry[name].max_concurrency,
                            task_id=task_id,
                            wait_timeout_seconds=0.0,
                        )
                        if lease is None:
                            blocked = True
                            break
                        acquired_leases.append((name, lease))
                if blocked:
                    for _, lease in acquired_leases:
                        if lease_manager is not None:
                            lease_manager.release(lease)
                    continue
                for name, lease in acquired_leases:
                    if leases is not None:
                        leases.setdefault(name, []).append(lease)
                for name in eligible_names:
                    states[name].inflight += 1
                return eligible_names
        time.sleep(0.05)


def _execute_single_request(
    request: GenerationRequest,
    *,
    item_id: str,
    adapters: dict[str, Any],
    output_dir: str,
    provider_preference: list[str] | None,
    registry: dict[str, ProviderCapability],
    states: dict[str, ProviderState],
    lock: threading.Lock,
    max_retries_per_provider: int,
    retry_delay_seconds: float,
    perf_log_path: str | None = None,
    lease_manager: ProviderLeaseManager | None = None,
    task_id: str | None = None,
) -> BatchItemResult:
    order = [name for name in _provider_order(request, provider_preference) if name in adapters]
    req_hash = request_hash(request)
    if not order:
        return BatchItemResult(
            item_id=item_id,
            request_hash=req_hash,
            request_type=request.request_type,
            status="failed",
            request_contract=_request_contract(request),
            error_code="no_provider",
            error_message="No eligible configured provider adapters are available for this item.",
        )

    attempted: list[str] = []
    attempt_records: list[dict[str, Any]] = []
    skipped_providers: list[dict[str, Any]] = []
    last_result: GenerationResult | None = None
    leases: dict[str, list[ProviderLease]] = {}
    health_aware = _is_health_aware_request(request)

    def _release_provider_from_future(future, provider_name: str) -> None:
        try:
            future.result()
        except Exception:
            pass
        _release_provider(provider_name, states, lock, lease_manager=lease_manager, leases=leases)

    def _run_provider_attempt(
        provider: str,
        *,
        retry_index: int,
        scheduling_wait_ms: int,
        health_aware_attempt: bool,
        extra_record: dict[str, Any] | None = None,
    ) -> tuple[GenerationResult, dict[str, Any]]:
        adapter = adapters[provider]
        health_check = getattr(adapter, "health_check", None)
        if health_aware_attempt and callable(health_check):
            preflight_result = health_check()
            if not preflight_result.ok:
                error_category, routing_action, cooldown_seconds, cooldown_until, remediation_hint = _apply_provider_health_result(
                    provider,
                    preflight_result,
                    states,
                    lock,
                    health_aware=True,
                )
                record = {
                    "provider": provider,
                    **_provider_pool_metadata(provider),
                    "comparison_contract": _comparison_contract_summary(provider, request, request),
                    "retry_index": retry_index,
                    "ok": False,
                    "preflight_check": True,
                    "latency_ms": preflight_result.latency_ms,
                    "scheduling_wait_ms": scheduling_wait_ms if retry_index == 0 else 0,
                    "timings_ms": dict(preflight_result.timings_ms or {}),
                    "error_category": error_category,
                    "failure_type": error_category,
                    "routing_action": routing_action,
                    "cooldown_seconds": cooldown_seconds,
                    "cooldown_until": cooldown_until,
                    "remediation_hint": remediation_hint,
                    "error_code": preflight_result.error_code,
                    "error_message": redact_text(preflight_result.error_message),
                    "provider_response_contract": _provider_response_contract(preflight_result),
                    "remote_task_id": _remote_task_id_from_result(preflight_result),
                    "artifact_metrics": dict(preflight_result.artifact_metrics or {}),
                    "artifact_dimensions": _artifact_dimensions(preflight_result.artifact_metrics),
                    "artifact_paths": list(preflight_result.artifact_paths),
                    "raw_response_path": preflight_result.raw_response_path,
                    **_reference_attempt_metrics(request, request),
                }
                if extra_record:
                    record.update(extra_record)
                return preflight_result, record
        provider_request = _materialize_request_for_provider(request, registry[provider])
        result = adapter.generate(provider_request)
        result = write_result(output_dir, provider, provider_request, result, perf_log_path=perf_log_path)
        error_category, routing_action, cooldown_seconds, cooldown_until, remediation_hint = _apply_provider_health_result(
            provider,
            result,
            states,
            lock,
            health_aware=health_aware_attempt,
        )
        record = {
            "provider": provider,
            **_provider_pool_metadata(provider),
            "comparison_contract": _comparison_contract_summary(provider, request, provider_request),
            "retry_index": retry_index,
            "ok": _effective_result_ok(result),
            "latency_ms": result.latency_ms,
            "scheduling_wait_ms": scheduling_wait_ms if retry_index == 0 else 0,
            "timings_ms": dict(result.timings_ms or {}),
            "error_category": error_category,
            "failure_type": error_category,
            "routing_action": routing_action,
            "cooldown_seconds": cooldown_seconds,
            "cooldown_until": cooldown_until,
            "remediation_hint": remediation_hint,
            "error_code": result.error_code,
            "error_message": redact_text(result.error_message),
            "provider_response_contract": _provider_response_contract(result),
            "remote_task_id": _remote_task_id_from_result(result),
            "artifact_metrics": dict(result.artifact_metrics or {}),
            "artifact_dimensions": _artifact_dimensions(result.artifact_metrics),
            "artifact_paths": list(result.artifact_paths),
            "raw_response_path": result.raw_response_path,
            **_reference_attempt_metrics(request, provider_request),
        }
        if extra_record:
            record.update(extra_record)
        return result, record

    if request.routing_mode == "race":
        race_order = [name for name in (request.race_providers or order) if name in order]
        deduped_race_order: list[str] = []
        seen_groups: set[str] = set()
        for name in race_order:
            if not _supports_request(registry[name], request):
                _record_unsupported_provider(skipped_providers, name, registry[name], request, states.get(name))
                continue
            state = states.get(name)
            if state is not None and _cooldown_active(state):
                _record_skipped_provider(skipped_providers, name, reason="cooldown", state=state)
                continue
            group = _provider_group(name)
            if group in seen_groups:
                continue
            seen_groups.add(group)
            deduped_race_order.append(name)
        if not deduped_race_order:
            return BatchItemResult(
                item_id=item_id,
                request_hash=req_hash,
                request_type=request.request_type,
                status="failed",
                request_contract=_request_contract(request),
                error_code=_no_provider_error_code(skipped_providers),
                error_message="No eligible race providers are available for this item.",
                race_requested_providers=race_order,
                skipped_providers=skipped_providers,
            )

        race_reservation_started = time.time()
        launched_providers = _reserve_race_providers(
            request,
            deduped_race_order,
            registry,
            states,
            lock,
            skipped_providers,
            lease_manager,
            leases,
            task_id,
        )
        race_scheduling_wait_ms = int((time.time() - race_reservation_started) * 1000)
        if not launched_providers:
            return BatchItemResult(
                item_id=item_id,
                request_hash=req_hash,
                request_type=request.request_type,
                status="failed",
                request_contract=_request_contract(request),
                error_code=_no_provider_error_code(skipped_providers),
                error_message="No race providers could be reserved for this item.",
                race_requested_providers=deduped_race_order,
                skipped_providers=skipped_providers,
            )

        def _run_once(provider: str):
            try:
                provider_request = _materialize_request_for_provider(request, registry[provider])
                result = adapters[provider].generate(provider_request)
                result = write_result(output_dir, provider, provider_request, result, perf_log_path=perf_log_path)
                error_category, routing_action, cooldown_seconds, cooldown_until, remediation_hint = _apply_provider_health_result(
                    provider,
                    result,
                    states,
                    lock,
                )
                record = {
                    "provider": provider,
                    **_provider_pool_metadata(provider),
                    "comparison_contract": _comparison_contract_summary(provider, request, provider_request),
                    "retry_index": 0,
                    "ok": _effective_result_ok(result),
                    "latency_ms": result.latency_ms,
                    "scheduling_wait_ms": race_scheduling_wait_ms,
                    "timings_ms": dict(result.timings_ms or {}),
                    "error_category": error_category,
                    "failure_type": error_category,
                    "routing_action": routing_action,
                    "cooldown_seconds": cooldown_seconds,
                    "cooldown_until": cooldown_until,
                    "remediation_hint": remediation_hint,
                    "error_code": result.error_code,
                    "error_message": redact_text(result.error_message),
                    "provider_response_contract": _provider_response_contract(result),
                    "remote_task_id": _remote_task_id_from_result(result),
                    "artifact_metrics": dict(result.artifact_metrics or {}),
                    "artifact_dimensions": _artifact_dimensions(result.artifact_metrics),
                    "artifact_paths": list(result.artifact_paths),
                    "raw_response_path": result.raw_response_path,
                    **_reference_attempt_metrics(request, provider_request),
                }
                return provider, result, record
            finally:
                _release_provider(provider, states, lock, lease_manager=lease_manager, leases=leases)

        completed_records: list[dict[str, Any]] = []
        last_race_result: GenerationResult | None = None
        race_pool = ThreadPoolExecutor(max_workers=len(launched_providers))
        future_map = {race_pool.submit(_run_once, provider): provider for provider in launched_providers}
        pending = set(future_map)

        try:
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    provider, result, record = future.result()
                    completed_records.append(record)
                    last_race_result = result
                    completed = [r["provider"] for r in completed_records]
                    if _effective_result_ok(result):
                        for other in pending:
                            other.cancel()
                        return BatchItemResult(
                            item_id=item_id,
                            request_hash=req_hash,
                            request_type=request.request_type,
                            status="success",
                            request_contract=_request_contract(request),
                            selected_provider=provider,
                            selected_provider_group=_provider_group(provider),
                            selected_provider_pool_identity=str(_provider_pool_metadata(provider)["pool_identity"]),
                            attempted_providers=launched_providers,
                            attempts=completed_records,
                            race_requested_providers=deduped_race_order,
                            race_launched_providers=launched_providers,
                            race_completed_providers=completed,
                            skipped_providers=skipped_providers,
                            artifact_paths=result.artifact_paths,
                            raw_response_path=result.raw_response_path,
                            latency_ms=result.latency_ms,
                        )
        finally:
            race_pool.shutdown(wait=True, cancel_futures=True)

        return BatchItemResult(
            item_id=item_id,
            request_hash=req_hash,
            request_type=request.request_type,
            status="failed",
            request_contract=_request_contract(request),
            selected_provider=launched_providers[-1] if launched_providers else None,
            selected_provider_group=_provider_group(launched_providers[-1]) if launched_providers else None,
            selected_provider_pool_identity=(
                str(_provider_pool_metadata(launched_providers[-1])["pool_identity"])
                if launched_providers
                else None
            ),
            attempted_providers=launched_providers,
            attempts=completed_records,
            race_requested_providers=deduped_race_order,
            race_launched_providers=launched_providers,
            race_completed_providers=[r["provider"] for r in completed_records],
            skipped_providers=skipped_providers,
            artifact_paths=last_race_result.artifact_paths if last_race_result else [],
            raw_response_path=last_race_result.raw_response_path if last_race_result else None,
            latency_ms=last_race_result.latency_ms if last_race_result else None,
            error_code=_failure_result_code(last_race_result, completed_records),
            error_message=_failure_result_message(
                last_race_result,
                completed_records,
                "No race provider attempt could be executed.",
            ),
        )

    if health_aware:
        remaining = [name for name in order if name not in attempted]
        acquire_started = time.time()
        provider = _acquire_provider(
            request,
            remaining,
            registry,
            states,
            lock,
            skipped_providers,
            lease_manager,
            leases,
            task_id,
        )
        scheduling_wait_ms = int((time.time() - acquire_started) * 1000)
        if provider is None:
            return BatchItemResult(
                item_id=item_id,
                request_hash=req_hash,
                request_type=request.request_type,
                status="failed",
                request_contract=_request_contract(request),
                attempted_providers=attempted,
                attempts=attempt_records,
                skipped_providers=skipped_providers,
                error_code=_no_provider_error_code(skipped_providers),
                error_message="No provider attempt could be executed.",
            )
        attempted.append(provider)

        primary_pool = ThreadPoolExecutor(max_workers=1)
        primary_future = primary_pool.submit(
            _run_provider_attempt,
            provider,
            retry_index=0,
            scheduling_wait_ms=scheduling_wait_ms,
            health_aware_attempt=True,
            extra_record=None,
        )
        backup_pool: ThreadPoolExecutor | None = None
        backup_provider: str | None = None
        backup_future = None
        backup_launch_ms: int | None = None
        backup_after_seconds = _health_aware_backup_after_seconds()
        try:
            done, pending = wait({primary_future}, timeout=backup_after_seconds, return_when=FIRST_COMPLETED)
            if not done and len(order) > 1:
                backup_remaining = [name for name in order if name not in attempted]
                backup_started = time.time()
                backup_provider = _acquire_provider(
                    request,
                    backup_remaining,
                    registry,
                    states,
                    lock,
                    skipped_providers,
                    lease_manager,
                    leases,
                    task_id,
                )
                backup_launch_ms = int((time.time() - acquire_started) * 1000)
                if backup_provider is not None:
                    attempted.append(backup_provider)
                    backup_pool = ThreadPoolExecutor(max_workers=1)
                    backup_future = backup_pool.submit(
                        _run_provider_attempt,
                        backup_provider,
                        retry_index=0,
                        scheduling_wait_ms=int((time.time() - backup_started) * 1000),
                        health_aware_attempt=True,
                        extra_record={
                            "routing_action": "backup_provider",
                            "backup_for_provider": provider,
                            "backup_launched_after_ms": backup_launch_ms,
                        },
                    )

            futures = {primary_future}
            if backup_future is not None:
                futures.add(backup_future)

            while futures:
                done, futures = wait(futures, return_when=FIRST_COMPLETED)
                for future in done:
                    result, record = future.result()
                    attempt_records.append(record)
                    last_result = result
                    current_provider = str(record["provider"])
                    _release_provider(current_provider, states, lock, lease_manager=lease_manager, leases=leases)
                    if _effective_result_ok(result):
                        selected = str(record["provider"])
                        for other in list(futures):
                            other_provider = provider if other is primary_future else backup_provider
                            other.cancel()
                            if other_provider:
                                other.add_done_callback(
                                    lambda done_future, provider_name=other_provider: _release_provider_from_future(
                                        done_future,
                                        provider_name,
                                    )
                                )
                        return BatchItemResult(
                            item_id=item_id,
                            request_hash=req_hash,
                            request_type=request.request_type,
                            status="success",
                            request_contract=_request_contract(request),
                            selected_provider=selected,
                            selected_provider_group=_provider_group(selected),
                            selected_provider_pool_identity=str(_provider_pool_metadata(selected)["pool_identity"]),
                            attempted_providers=attempted,
                            attempts=attempt_records,
                            skipped_providers=skipped_providers,
                            artifact_paths=result.artifact_paths,
                            raw_response_path=result.raw_response_path,
                            latency_ms=result.latency_ms,
                        )
                    if not request.allow_fallback or not _should_failover(result):
                        futures.clear()
                        break
                    if backup_future is None and str(record["provider"]) == provider:
                        break
                if backup_future is None:
                    break
        finally:
            primary_pool.shutdown(wait=False, cancel_futures=True)
            if backup_pool is not None:
                backup_pool.shutdown(wait=False, cancel_futures=True)

        while request.allow_fallback:
            remaining = [name for name in order if name not in attempted]
            acquire_started = time.time()
            provider = _acquire_provider(
                request,
                remaining,
                registry,
                states,
                lock,
                skipped_providers,
                lease_manager,
                leases,
                task_id,
            )
            scheduling_wait_ms = int((time.time() - acquire_started) * 1000)
            if provider is None:
                break
            attempted.append(provider)
            result, record = _run_provider_attempt(
                provider,
                retry_index=0,
                scheduling_wait_ms=scheduling_wait_ms,
                health_aware_attempt=True,
            )
            attempt_records.append(record)
            last_result = result
            _release_provider(provider, states, lock, lease_manager=lease_manager, leases=leases)
            if _effective_result_ok(result):
                return BatchItemResult(
                    item_id=item_id,
                    request_hash=req_hash,
                    request_type=request.request_type,
                    status="success",
                    request_contract=_request_contract(request),
                    selected_provider=provider,
                    selected_provider_group=_provider_group(provider),
                    selected_provider_pool_identity=str(_provider_pool_metadata(provider)["pool_identity"]),
                    attempted_providers=attempted,
                    attempts=attempt_records,
                    skipped_providers=skipped_providers,
                    artifact_paths=result.artifact_paths,
                    raw_response_path=result.raw_response_path,
                    latency_ms=result.latency_ms,
                )
            if not _should_failover(result):
                break

        return BatchItemResult(
            item_id=item_id,
            request_hash=req_hash,
            request_type=request.request_type,
            status="failed",
            request_contract=_request_contract(request),
            selected_provider=attempted[-1] if attempted else None,
            selected_provider_group=_provider_group(attempted[-1]) if attempted else None,
            selected_provider_pool_identity=(
                str(_provider_pool_metadata(attempted[-1])["pool_identity"])
                if attempted
                else None
            ),
            attempted_providers=attempted,
            attempts=attempt_records,
            skipped_providers=skipped_providers,
            artifact_paths=last_result.artifact_paths if last_result else [],
            raw_response_path=last_result.raw_response_path if last_result else None,
            latency_ms=last_result.latency_ms if last_result else None,
            error_code=(
                _failure_result_code(last_result, attempt_records)
                if last_result
                else _no_provider_error_code(skipped_providers)
            ),
            error_message=_failure_result_message(
                last_result,
                attempt_records,
                "No provider attempt could be executed.",
            ),
        )

    while True:
        remaining = [name for name in order if name not in attempted]
        acquire_started = time.time()
        provider = _acquire_provider(
            request,
            remaining,
            registry,
            states,
            lock,
            skipped_providers,
            lease_manager,
            leases,
            task_id,
        )
        scheduling_wait_ms = int((time.time() - acquire_started) * 1000)
        if provider is None:
            break
        attempted.append(provider)

        provider_result: GenerationResult | None = None
        for retry_index in range(max_retries_per_provider + 1):
            result, record = _run_provider_attempt(
                provider,
                retry_index=retry_index,
                scheduling_wait_ms=scheduling_wait_ms,
                health_aware_attempt=False,
            )
            provider_result = result
            last_result = result
            attempt_records.append(record)

            if _effective_result_ok(result):
                _release_provider(provider, states, lock, lease_manager=lease_manager, leases=leases)
                return BatchItemResult(
                    item_id=item_id,
                    request_hash=req_hash,
                    request_type=request.request_type,
                    status="success",
                    request_contract=_request_contract(request),
                    selected_provider=provider,
                    selected_provider_group=_provider_group(provider),
                    selected_provider_pool_identity=str(_provider_pool_metadata(provider)["pool_identity"]),
                    attempted_providers=attempted,
                    attempts=attempt_records,
                    skipped_providers=skipped_providers,
                    artifact_paths=result.artifact_paths,
                    raw_response_path=result.raw_response_path,
                    latency_ms=result.latency_ms,
                )
            decision = classify_generation_failure(result)
            if retry_index < max_retries_per_provider and should_retry_same_provider(decision):
                time.sleep(retry_delay_seconds)
                continue
            break

        _release_provider(provider, states, lock, lease_manager=lease_manager, leases=leases)
        if not request.allow_fallback or not provider_result or not _should_failover(provider_result):
            break

    return BatchItemResult(
        item_id=item_id,
        request_hash=req_hash,
        request_type=request.request_type,
        status="failed",
        request_contract=_request_contract(request),
        selected_provider=attempted[-1] if attempted else None,
        selected_provider_group=_provider_group(attempted[-1]) if attempted else None,
        selected_provider_pool_identity=(
            str(_provider_pool_metadata(attempted[-1])["pool_identity"])
            if attempted
            else None
        ),
        attempted_providers=attempted,
        attempts=attempt_records,
        skipped_providers=skipped_providers,
        artifact_paths=last_result.artifact_paths if last_result else [],
        raw_response_path=last_result.raw_response_path if last_result else None,
        latency_ms=last_result.latency_ms if last_result else None,
        error_code=(
            _failure_result_code(last_result, attempt_records)
            if last_result
            else _no_provider_error_code(skipped_providers)
        ),
        error_message=_failure_result_message(
            last_result,
            attempt_records,
            "No provider attempt could be executed.",
        ),
    )


def _explain_single_request(
    request: GenerationRequest,
    *,
    item_id: str,
    provider_preference: list[str] | None,
    registry: dict[str, ProviderCapability],
    states: dict[str, ProviderState],
    policy_plan: RoutingPolicyPlan,
) -> BatchItemResult:
    order = [name for name in policy_plan.provider_order if name in registry]
    skipped_providers: list[dict[str, Any]] = []
    capability_decisions: list[dict[str, Any]] = []
    probe_decisions: list[dict[str, Any]] = []
    planned: list[str] = []
    for name in order:
        cap = registry[name]
        state = states[name]
        capability = check_provider_capability(cap, request)
        capability_decisions.append({
            "provider": name,
            **_provider_pool_metadata(name),
            "comparison_contract": _comparison_contract_summary(name, request),
            "supported": capability.supported,
            "reason": capability.reason,
            "required_reference_count": capability.required_reference_count,
            "max_reference_images": capability.max_reference_images,
            "will_downgrade_references": capability.will_downgrade_references,
        })
        probe = probe_decision(name, state, requested=False)
        probe_decisions.append({
            "provider": name,
            **_provider_pool_metadata(name),
            "comparison_contract": _comparison_contract_summary(name, request),
            "allowed": probe.allowed,
            "reason": probe.reason,
            "probe_type": probe.probe_type,
            "real_image_generation": probe.real_image_generation,
            "next_allowed_at": probe.next_allowed_at,
        })
        if _cooldown_active(state):
            _record_skipped_provider(skipped_providers, name, reason="cooldown", state=state)
            continue
        if not capability.supported:
            _record_unsupported_provider(skipped_providers, name, cap, request, state)
            continue
        planned.append(name)

    result = BatchItemResult(
        item_id=item_id,
        request_hash=request_hash(request),
        request_type=request.request_type,
        status="planned" if planned else "failed",
        request_contract=_request_contract(request),
        race_requested_providers=list(policy_plan.race_providers),
        skipped_providers=skipped_providers,
        error_code=None if planned else _no_provider_error_code(skipped_providers),
        error_message=None if planned else "No provider is currently eligible for this dry-run plan.",
    )
    result.planned_providers = planned or order or list(provider_preference or [])
    result.capability_decisions = capability_decisions
    result.probe_decisions = probe_decisions
    result = _attach_policy_result(result, policy_plan)
    result.planned_providers = planned
    return result



def run_job_file(
    path: str,
    *,
    providers: list[str] | None = None,
    output_dir: str,
    batch_dir: str,
    ledger_dir: str,
    perf_log_path: str | None = None,
    provider_state_path: str | None = None,
    default_profile: str = "generic",
    max_workers: int | None = None,
    max_retries_per_provider: int = 1,
    retry_delay_seconds: float = 2.0,
    routing_policy: str | None = None,
    provider_tier: str | None = None,
    preset_name: str | None = None,
    routing_preset: RoutingPreset | None = None,
    provider_lock_dir: str | None = None,
    cross_process_provider_locks: bool = False,
    task_id: str | None = None,
    dry_run: bool = False,
    async_submit_only: bool = False,
) -> BatchRunResult:
    job_type, items = load_job_items_with_defaults(
        path,
        default_profile=default_profile,
        default_routing_policy=routing_policy,
        default_provider_tier=provider_tier,
    )
    if routing_preset and routing_preset.model_selection == "simage-v1":
        allowed = providers if providers is not None else routing_preset.providers
        selected_items = []
        for item_id, request, item_providers in items:
            candidates = item_providers if item_providers is not None else allowed
            if any(name not in allowed for name in candidates):
                raise ValueError("Item provider exceeds the Simage preset/provider restriction")
            selection = select_simage_model(request, candidates)
            request = _with_request_metadata(
                request,
                {
                    "modelSelection": asdict(selection),
                    "providerRestriction": [selection.provider],
                },
            )
            selected_items.append((item_id, request, [selection.provider]))
        items = selected_items
        # Build only selected models. Never silently substitute another model on failure.
        providers = list(dict.fromkeys(name for _, _, names in items for name in names))
    adapters: dict[str, Any] = {}
    startup_errors: dict[str, str] = {}
    if dry_run:
        registry = provider_capability_registry(providers)
    else:
        adapters, startup_errors = _build_adapters(providers)
        registry = {name: adapters[name].capability() for name in adapters}
    state_store = ProviderHealthStore(provider_state_path) if provider_state_path else None
    states = state_store.load(registry) if state_store else {name: ProviderState() for name in registry}
    lock = threading.Lock()
    lease_manager = (
        ProviderLeaseManager(provider_lock_dir)
        if provider_lock_dir and cross_process_provider_locks and not dry_run
        else None
    )

    # Batch throughput is item-concurrent by default. Provider/lane routing decides
    # where each item starts; it must not shrink the batch to provider-group rounds.
    worker_count = max_workers or max(len(items), 1)
    indexed_results: list[tuple[int, BatchItemResult]] = []
    latency_budget_ms = (
        int(routing_preset.latency_budget_seconds * 1000)
        if routing_preset and routing_preset.latency_budget_seconds
        else None
    )
    preset_metadata: dict[str, Any] = {}
    if routing_preset and routing_preset.artifact_min_width:
        preset_metadata["artifactMinWidth"] = routing_preset.artifact_min_width
    if routing_preset and routing_preset.artifact_min_height:
        preset_metadata["artifactMinHeight"] = routing_preset.artifact_min_height
    if routing_preset and routing_preset.artifact_dimension_mode:
        preset_metadata["artifactDimensionMode"] = routing_preset.artifact_dimension_mode

    def _target_count_from_items() -> int | None:
        for _, item_request, _ in items:
            raw = item_request.metadata.get("targetSuccessCount") or item_request.metadata.get("target_success_count")
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return None
            return value if value > 0 else None
        return None

    def _max_replacements_from_items() -> int:
        for _, item_request, _ in items:
            raw = item_request.metadata.get("maxReplacementItems") or item_request.metadata.get("max_replacement_items")
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                return 0
            return max(value, 0)
        return 0

    target_success_count = _target_count_from_items()
    max_replacement_items = _max_replacements_from_items() if target_success_count else 0

    if dry_run:
        perf_events = _read_perf_events(perf_log_path)
        scores_by_provider = {
            str(row["provider"]): row
            for row in provider_scores(perf_events, window_seconds=_perf_window_seconds())
        }
        scores_by_pool = {
            str(row["pool_identity"]): row
            for row in pool_scores(perf_events, window_seconds=_perf_window_seconds())
        }
        for idx, (item_id, request, item_providers) in enumerate(items):
            preference = list(item_providers) if item_providers else providers
            order = [name for name in _provider_order(request, preference) if name in registry]
            requested_policy = _request_policy_name(request, routing_policy)
            normalized_policy = _normalized_policy_name(requested_policy)
            if normalized_policy in {
                "load_balance",
                "balanced",
                "throughput_balanced",
                "health_aware_load_balance",
                "health_aware_balanced",
                "health_balanced",
            }:
                order = _spread_provider_order(order, idx)
            elif normalized_policy in {"weighted_load_balance", "weighted_balanced", "performance_balanced"}:
                order = _weighted_provider_order(order, idx, scores_by_provider, scores_by_pool, latency_budget_ms)
            order, spring_back_skips = _apply_spring_back_order(order, states, routing_preset, idx)
            plan = resolve_routing_policy(requested_policy, order)
            effective_request = _apply_policy_to_request(request, plan)
            if routing_preset and routing_preset.default_size:
                effective_request = _with_request_defaults(effective_request, size=routing_preset.default_size)
            if preset_metadata:
                effective_request = _with_request_metadata(effective_request, preset_metadata)
            explained = _explain_single_request(
                    effective_request,
                    item_id=item_id,
                    provider_preference=preference,
                    registry=registry,
                    states=states,
                    policy_plan=plan,
                )
            indexed_results.append((idx, _attach_pre_policy_skips(explained, spring_back_skips)))
    else:
        perf_events = _read_perf_events(perf_log_path)
        scores_by_provider = {
            str(row["provider"]): row
            for row in provider_scores(perf_events, window_seconds=_perf_window_seconds())
        }
        scores_by_pool = {
            str(row["pool_identity"]): row
            for row in pool_scores(perf_events, window_seconds=_perf_window_seconds())
        }
        replacement_item_count = 0
        pending_items: list[tuple[int, str, GenerationRequest, list[str] | None, dict[str, Any]]] = [
            (idx, item_id, request, item_providers, {})
            for idx, (item_id, request, item_providers) in enumerate(items)
        ]
        next_result_index = len(pending_items)

        def _submit_item(
            pool: ThreadPoolExecutor,
            idx: int,
            item_id: str,
            request: GenerationRequest,
            item_providers: list[str] | None,
            replacement_meta: dict[str, Any],
            future_map: dict[Any, int],
            future_policy: dict[Any, tuple[RoutingPolicyPlan, list[dict[str, Any]], dict[str, Any]]],
        ) -> None:
            started_at = time.time()
            request = _with_request_metadata(request, {"batchItemId": item_id})
            preference = list(item_providers) if item_providers else providers
            order = [name for name in _provider_order(request, preference) if name in registry]
            requested_policy = _request_policy_name(request, routing_policy)
            normalized_policy = _normalized_policy_name(requested_policy)
            if normalized_policy in {
                "load_balance",
                "balanced",
                "throughput_balanced",
                "health_aware_load_balance",
                "health_aware_balanced",
                "health_balanced",
            }:
                order = _spread_provider_order(order, idx)
            elif normalized_policy in {"weighted_load_balance", "weighted_balanced", "performance_balanced"}:
                order = _weighted_provider_order(order, idx, scores_by_provider, scores_by_pool, latency_budget_ms)
            order, spring_back_skips = _apply_spring_back_order(order, states, routing_preset, idx)
            plan = resolve_routing_policy(requested_policy, order)
            effective_request = _apply_policy_to_request(request, plan)
            if routing_preset and routing_preset.default_size:
                effective_request = _with_request_defaults(effective_request, size=routing_preset.default_size)
            if preset_metadata:
                effective_request = _with_request_metadata(effective_request, preset_metadata)
            if async_submit_only:
                effective_request = _with_request_metadata(effective_request, {"async_submit_only": True})
            effective_retries = (
                plan.max_retries_per_provider
                if plan.max_retries_per_provider is not None
                else max_retries_per_provider
            )
            future = pool.submit(
                _execute_single_request,
                effective_request,
                item_id=item_id,
                adapters=adapters,
                output_dir=output_dir,
                provider_preference=plan.provider_order,
                registry=registry,
                states=states,
                lock=lock,
                max_retries_per_provider=effective_retries,
                retry_delay_seconds=retry_delay_seconds,
                perf_log_path=perf_log_path,
                lease_manager=lease_manager,
                task_id=task_id or item_id,
            )
            meta = {**replacement_meta, "replacement_started_at": started_at}
            if replacement_meta:
                meta["replacement_provider_plan"] = list(plan.provider_order)
            future_map[future] = idx
            future_policy[future] = (plan, spring_back_skips, meta)

        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            future_map: dict[Any, int] = {}
            future_policy: dict[Any, tuple[RoutingPolicyPlan, list[dict[str, Any]], dict[str, Any]]] = {}
            while pending_items and len(future_map) < worker_count:
                _submit_item(pool, *pending_items.pop(0), future_map, future_policy)

            while future_map:
                done, _ = wait(set(future_map), return_when=FIRST_COMPLETED)
                for future in done:
                    idx = future_map.pop(future)
                    plan, spring_back_skips, replacement_meta = future_policy.pop(future)
                    item_result = _attach_policy_result(future.result(), plan)
                    if replacement_meta:
                        item_result.replacement_for_item_id = replacement_meta.get("replacement_for_item_id")
                        item_result.replacement_reason = replacement_meta.get("replacement_reason")
                        item_result.replacement_created_at = replacement_meta.get("replacement_created_at")
                        item_result.replacement_started_at = replacement_meta.get("replacement_started_at")
                        item_result.replacement_provider_plan = list(replacement_meta.get("replacement_provider_plan") or [])
                    indexed_results.append((idx, _attach_pre_policy_skips(item_result, spring_back_skips)))

                    if (
                        target_success_count
                        and item_result.status != "success"
                        and replacement_item_count < max_replacement_items
                    ):
                        completed_successes = sum(1 for _, row in indexed_results if row.status == "success")
                        running_count = len(future_map)
                        queued_count = len(pending_items)
                        if completed_successes + running_count + queued_count < target_success_count:
                            replacement_item_count += 1
                            replacement_id = f"{item_result.item_id}-repl-{replacement_item_count:03d}"
                            replacement_source = next(
                                (row for row in items if row[0] == item_result.item_id),
                                (items[0][0], items[0][1], items[0][2]),
                            )
                            pending_items.append((
                                next_result_index,
                                replacement_id,
                                _clone_request(replacement_source[1]),
                                replacement_source[2],
                                {
                                    "replacement_for_item_id": item_result.item_id,
                                    "replacement_reason": "terminal_item_failure",
                                    "replacement_created_at": time.time(),
                                },
                            ))
                            next_result_index += 1

                while pending_items and len(future_map) < worker_count:
                    _submit_item(pool, *pending_items.pop(0), future_map, future_policy)

    indexed_results.sort(key=lambda row: row[0])
    results = [row[1] for row in indexed_results]

    successful_statuses = {"success", "planned"} if dry_run else {"success"}
    success_count = sum(1 for r in results if r.status in successful_statuses)
    failure_count = len(results) - success_count
    if state_store:
        state_store.save(states)
    item_rows = [asdict(r) for r in results]
    batch_attempt_metrics = summarize_batch_attempts(item_rows)
    recovery_events = recovery_events_from_items(item_rows)
    remediation_events = remediation_events_from_items(item_rows)
    remediation_event_summary = remediation_summary(remediation_events)

    run_dir = Path(batch_dir) / _now_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    summary_path = run_dir / "run-summary.json"
    summary = {
        "schema_version": "batch-run-summary/v1",
        "dry_run": dry_run,
        "job_type": job_type,
        "input_path": str(Path(path).resolve()),
        "success_count": success_count,
        "failure_count": failure_count,
        "providers_requested": providers or ordered_lane_ids() or KNOWN_PROVIDERS,
        "startup_errors": startup_errors,
        "provider_state_path": provider_state_path,
        "provider_lock_dir": provider_lock_dir,
        "cross_process_provider_locks": bool(cross_process_provider_locks),
        "queue_task_id": task_id,
        "routing_preset": preset_name,
        "latency_budget_seconds": routing_preset.latency_budget_seconds if routing_preset else None,
        "perf_log_path": perf_log_path,
        "provider_health": state_store.snapshot(states) if state_store else {},
        "recovery_events": recovery_events,
        "remediation_events": remediation_events,
        "remediation_summary": remediation_event_summary,
        "secret_values_included": False,
        "max_workers": worker_count,
        "target_success_count": target_success_count,
        "max_replacement_items": max_replacement_items,
        "replacement_item_count": sum(1 for r in results if r.replacement_for_item_id),
        "max_retries_per_provider": max_retries_per_provider,
        "retry_delay_seconds": retry_delay_seconds,
        **batch_attempt_metrics,
        "items": item_rows,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    ledger_path = write_batch_ledger(ledger_dir, summary)
    append_batch_summary(summary, perf_log_path=perf_log_path)

    return BatchRunResult(
        job_type=job_type,
        item_results=results,
        summary_path=str(summary_path),
        ledger_path=str(ledger_path),
        success_count=success_count,
        failure_count=failure_count,
        dry_run=dry_run,
    )
