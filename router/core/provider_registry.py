from __future__ import annotations

import os
from pathlib import Path

from router.adapters.apiyi_gemini import ApiyiGeminiAdapter
from router.adapters.base import BaseAdapter
from router.adapters.aifast_gemini import AifastGeminiAdapter
from router.adapters.aifast_openai_compat import AifastOpenAICompatAdapter
from router.adapters.aiwave_gemini import AiWaveGeminiAdapter
from router.adapters.gptge_openai_images import GptGeOpenAIImagesAdapter
from router.adapters.relay_openai_images import ApiyiOpenAIImagesAdapter, LaozhangOpenAIImagesAdapter
from router.adapters.vectorengine_gemini import VectorEngineGeminiAdapter
from router.adapters.vectorengine_openai_compat import VectorEngineOpenAICompatAdapter
from router.core.models import CapabilityBucket, ProviderCapability
from router.core.source_config import LaneConfig, load_lane_configs, ordered_lane_ids


PROVIDER_GROUPS = {
    "vectorengine": "vectorengine-group",
    "vectorengine_compat": "vectorengine-group",
    "aifast": "aifast-group",
    "aifast_compat": "aifast-group",
    "aiwave": "aiwave-group",
    "gptge": "gptge-group",
    "laozhang": "laozhang-group",
    "apiyi": "apiyi-group",
    "rightcodes": "rightcodes-group",
    "apimart": "apimart-group",
}

PROVIDER_TIERS = {
    "vectorengine": "daily",
    "vectorengine_compat": "daily",
    "aifast": "daily",
    "aifast_compat": "daily",
    "aiwave": "daily",
    "gptge": "daily",
    "laozhang": "professional",
    "apiyi": "professional",
    "rightcodes": "professional",
    "apimart": "professional",
}


def _lane_pool_identity(lane: LaneConfig) -> str:
    if lane.pool_identity:
        return lane.pool_identity
    pool_parts = [
        lane.provider_group,
        lane.station_id or "station-unknown",
        lane.account_group or lane.account_id or "account-unknown",
    ]
    return "/".join(pool_parts)


PROVIDER_ENV = {
    "vectorengine": {
        "api_key_env": "MIR_VECTORENGINE_API_KEY",
        "base_url_env": "MIR_VECTORENGINE_BASE_URL",
        "model_env": "MIR_VECTORENGINE_MODEL",
    },
    "vectorengine_compat": {
        "api_key_env": "MIR_VECTORENGINE_API_KEY",
        "base_url_env": "MIR_VECTORENGINE_BASE_URL",
        "model_env": "MIR_VECTORENGINE_COMPAT_MODEL",
    },
    "aifast": {
        "api_key_env": "MIR_AIFAST_API_KEY",
        "base_url_env": "MIR_AIFAST_BASE_URL",
        "model_env": "MIR_AIFAST_MODEL",
    },
    "aifast_compat": {
        "api_key_env": "MIR_AIFAST_API_KEY",
        "base_url_env": "MIR_AIFAST_BASE_URL",
        "model_env": "MIR_AIFAST_COMPAT_MODEL",
    },
    "aiwave": {
        "api_key_env": "MIR_AIWAVE_API_KEY",
        "base_url_env": "MIR_AIWAVE_BASE_URL",
        "model_env": "MIR_AIWAVE_MODEL",
    },
    "gptge": {
        "api_key_env": "MIR_GPTGE_API_KEY",
        "base_url_env": "MIR_GPTGE_BASE_URL",
        "model_env": "MIR_GPTGE_MODEL",
    },
    "laozhang": {
        "api_key_env": "MIR_LAOZHANG_API_KEY",
        "base_url_env": "MIR_LAOZHANG_BASE_URL",
        "model_env": "MIR_LAOZHANG_MODEL",
    },
    "apiyi": {
        "api_key_env": "MIR_APIYI_API_KEY",
        "base_url_env": "MIR_APIYI_BASE_URL",
        "model_env": "MIR_APIYI_MODEL",
    },
    "rightcodes": {
        "api_key_env": "MIR_RIGHTCODES_API_KEY",
        "base_url_env": "MIR_RIGHTCODES_BASE_URL",
        "model_env": "MIR_RIGHTCODES_MODEL",
    },
    "apimart": {
        "api_key_env": "MIR_APIMART_API_KEY",
        "base_url_env": "MIR_APIMART_BASE_URL",
        "model_env": "MIR_APIMART_MODEL",
    },
}


def _read_phase1_defaults() -> dict:
    root = Path(__file__).resolve().parents[2]
    config_path = root / "configs" / "providers.phase1-defaults.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _load_phase1_defaults() -> None:
    """Load non-secret provider defaults from configs/providers.phase1-defaults.yaml.

    This config file is a defaults/config surface, not a secret store. We only hydrate
    base_url / model / timeout style env vars when they are absent, while API keys must
    still come from real environment variables.
    """
    data = _read_phase1_defaults()

    env_map = {
        "vectorengine_native": {
            "base_url": "MIR_VECTORENGINE_BASE_URL",
            "model": "MIR_VECTORENGINE_MODEL",
        },
        "vectorengine_compat": {
            "base_url": "MIR_VECTORENGINE_BASE_URL",
            "model": "MIR_VECTORENGINE_COMPAT_MODEL",
        },
        "gptge": {
            "base_url": "MIR_GPTGE_BASE_URL",
            "model": "MIR_GPTGE_MODEL",
        },
        "aiwave": {
            "base_url": "MIR_AIWAVE_BASE_URL",
            "model": "MIR_AIWAVE_MODEL",
        },
        "aifast": {
            "base_url": "MIR_AIFAST_BASE_URL",
            "model": "MIR_AIFAST_MODEL",
        },
        "laozhang": {
            "base_url": "MIR_LAOZHANG_BASE_URL",
            "model": "MIR_LAOZHANG_MODEL",
        },
        "apiyi": {
            "base_url": "MIR_APIYI_BASE_URL",
            "model": "MIR_APIYI_MODEL",
        },
        "rightcodes": {
            "base_url": "MIR_RIGHTCODES_BASE_URL",
            "model": "MIR_RIGHTCODES_MODEL",
        },
        "apimart": {
            "base_url": "MIR_APIMART_BASE_URL",
            "model": "MIR_APIMART_MODEL",
        },
    }

    for provider_name, mapping in env_map.items():
        section = data.get(provider_name) or {}
        for field_name, env_name in mapping.items():
            if not os.environ.get(env_name):
                value = section.get(field_name)
                if value:
                    os.environ[env_name] = str(value)


def provider_startup_diagnostics() -> list[dict[str, object]]:
    defaults = _read_phase1_defaults()
    lanes = load_lane_configs()
    provider_sections = {
        "vectorengine": "vectorengine_native",
        "vectorengine_compat": "vectorengine_compat",
        "aifast": "aifast",
        "aifast_compat": "aifast_compat",
        "aiwave": "aiwave",
        "gptge": "gptge",
        "laozhang": "laozhang",
        "apiyi": "apiyi",
        "rightcodes": "rightcodes",
        "apimart": "apimart",
    }
    diagnostics: list[dict[str, object]] = []
    for provider, env_names in PROVIDER_ENV.items():
        section = defaults.get(provider_sections[provider], {}) or {}
        api_key_env = str(env_names["api_key_env"])
        base_url_env = str(env_names["base_url_env"])
        model_env = str(env_names["model_env"])
        diagnostics.append({
            "provider": provider,
            "provider_group": section.get("provider_group") or PROVIDER_GROUPS[provider],
            "pool_identity": section.get("provider_group") or PROVIDER_GROUPS[provider],
            "enabled": bool(section.get("enabled", True)),
            "api_key_env": api_key_env,
            "key_present": bool(os.environ.get(api_key_env)),
            "base_url": os.environ.get(base_url_env) or section.get("base_url"),
            "model": os.environ.get(model_env) or section.get("model"),
            "capability_bucket": section.get("capability_bucket"),
            "max_concurrency": section.get("max_concurrency"),
        })
    for lane in lanes.values():
        diagnostics.append({
            "provider": lane.lane_id,
            "base_provider": lane.provider,
            "provider_group": lane.provider_group,
            "pool_identity": _lane_pool_identity(lane),
            "station_id": lane.station_id,
            "account_id": lane.account_id,
            "account_group": lane.account_group,
            "credential_id": lane.credential_id,
            "route_id": lane.route_id,
            "source_tier": lane.source_tier,
            "enabled": lane.enabled,
            "api_key_env": lane.api_key_env,
            "key_present": bool(os.environ.get(lane.api_key_env)),
            "base_url": lane.base_url,
            "route_path": lane.route_path,
            "model": lane.model,
            "model_class": lane.model_class,
            "default_size": lane.default_size,
            "degradation_status": lane.degradation_status,
            "capability_bucket": lane.capability_bucket,
            "max_reference_images": lane.max_reference_images,
            "max_concurrency": lane.max_concurrency,
            "image_contract": lane.image_contract,
            "is_lane": True,
        })
    return diagnostics


def provider_capability_registry(providers: list[str] | None = None) -> dict[str, ProviderCapability]:
    lane_ids = ordered_lane_ids()
    default_providers = lane_ids if lane_ids else list(PROVIDER_ENV.keys())
    requested = set(name.strip().lower() for name in (providers or default_providers))
    registry: dict[str, ProviderCapability] = {}
    for row in provider_startup_diagnostics():
        provider = str(row["provider"])
        if provider not in requested:
            continue
        degradation_status = str(row.get("degradation_status") or "active")
        bucket_value = str(row.get("capability_bucket") or "IM")
        try:
            bucket = CapabilityBucket(bucket_value)
        except ValueError:
            bucket = CapabilityBucket.MULTI_REFERENCE
        max_reference_images = row.get("max_reference_images")
        if max_reference_images is None:
            max_reference_images = 5 if bucket == CapabilityBucket.MULTI_REFERENCE else (1 if bucket == CapabilityBucket.SINGLE_REFERENCE else 0)
        registry[provider] = ProviderCapability(
            provider=provider,
            bucket=bucket,
            supports_text_to_image=bucket in {
                CapabilityBucket.TEXT_ONLY,
                CapabilityBucket.SINGLE_REFERENCE,
                CapabilityBucket.MULTI_REFERENCE,
            },
            supports_image_to_image=bucket in {
                CapabilityBucket.SINGLE_REFERENCE,
                CapabilityBucket.MULTI_REFERENCE,
            },
            supports_multi_reference=bucket == CapabilityBucket.MULTI_REFERENCE,
            max_reference_images=int(max_reference_images),
            max_concurrency=int(row.get("max_concurrency") or 1),
            enabled=bool(row.get("enabled", True)) and degradation_status != "degraded_excluded",
        )
    return registry


def provider_group_for(name: str) -> str:
    normalized = name.strip().lower()
    lanes = load_lane_configs()
    if normalized in lanes:
        return lanes[normalized].provider_group
    return PROVIDER_GROUPS.get(normalized, normalized)


def provider_pool_metadata_for(name: str) -> dict[str, object]:
    normalized = name.strip().lower()
    lanes = load_lane_configs()
    if normalized in lanes:
        lane = lanes[normalized]
        return {
            "provider_group": lane.provider_group,
            "pool_identity": _lane_pool_identity(lane),
            "station_id": lane.station_id,
            "account_id": lane.account_id,
            "account_group": lane.account_group,
            "credential_id": lane.credential_id,
            "route_id": lane.route_id,
            "route_path": lane.route_path,
            "model": lane.model,
            "model_class": lane.model_class,
            "production_line": str(lane.model_class).lower() if lane.model_class else None,
            "default_size": lane.default_size,
            "degradation_status": lane.degradation_status or "active",
            "image_contract": {
                "route_path": lane.route_path,
                "textToImagePath": lane.image_contract.get("textToImagePath") or lane.image_contract.get("text_to_image_path"),
                "requestFormat": lane.image_contract.get("requestFormat") or lane.image_contract.get("request_format"),
                "textToImageRequestFormat": (
                    lane.image_contract.get("textToImageRequestFormat")
                    or lane.image_contract.get("text_to_image_request_format")
                ),
                "requestSizeMode": lane.image_contract.get("requestSizeMode") or lane.image_contract.get("request_size_mode"),
                "defaultSize": lane.image_contract.get("defaultSize") or lane.image_contract.get("default_size"),
                "defaultResolution": lane.image_contract.get("defaultResolution") or lane.image_contract.get("default_resolution"),
                "referenceField": lane.image_contract.get("referenceField") or lane.image_contract.get("reference_field"),
                "imageUrlsField": lane.image_contract.get("imageUrlsField") or lane.image_contract.get("image_urls_field"),
                "pollPath": lane.image_contract.get("pollPath") or lane.image_contract.get("poll_path"),
                "pollQuery": lane.image_contract.get("pollQuery") or lane.image_contract.get("poll_query"),
                "pollInitialDelaySeconds": (
                    lane.image_contract.get("pollInitialDelaySeconds")
                    or lane.image_contract.get("poll_initial_delay_seconds")
                ),
                "asyncRemote": lane.async_remote,
            },
        }
    provider_group = PROVIDER_GROUPS.get(normalized, normalized)
    return {
        "provider_group": provider_group,
        "pool_identity": provider_group,
        "station_id": None,
        "account_id": None,
        "account_group": None,
        "credential_id": None,
        "route_id": None,
        "route_path": None,
        "model": None,
        "model_class": None,
        "production_line": None,
        "default_size": None,
        "degradation_status": "active",
        "image_contract": {},
    }


def provider_tier_for(name: str) -> str:
    normalized = name.strip().lower()
    lanes = load_lane_configs()
    if normalized in lanes:
        return lanes[normalized].source_tier
    return PROVIDER_TIERS.get(normalized, "daily")


_load_phase1_defaults()


def _extra_headers_from_env(mapping: dict[str, str]) -> dict[str, str]:
    return {
        header_name: value
        for header_name, env_name in mapping.items()
        if (value := os.environ.get(env_name))
    }


def _timeout_value(lane: LaneConfig, fallback_env: str, fallback_value: str) -> int:
    if lane.timeout_seconds is not None:
        return lane.timeout_seconds
    if lane.timeout_env and os.environ.get(lane.timeout_env):
        return int(os.environ[lane.timeout_env])
    return int(os.environ.get(fallback_env, fallback_value))


def _preflight_timeout_value(lane: LaneConfig) -> float | None:
    return lane.preflight_timeout_seconds


def _lane_extra_headers(lane: LaneConfig, defaults: dict[str, str] | None = None) -> dict[str, str]:
    headers = _extra_headers_from_env(defaults or {})
    headers.update(_extra_headers_from_env(lane.extra_header_envs))
    headers.update({name: value for name, value in lane.extra_headers.items() if value})
    return headers


def _apply_lane_identity(adapter: BaseAdapter, lane: LaneConfig) -> BaseAdapter:
    adapter.provider_name = lane.lane_id
    if hasattr(adapter, "async_remote"):
        setattr(adapter, "async_remote", lane.async_remote)
    original_capability = adapter.capability

    def lane_capability() -> ProviderCapability:
        base = original_capability()
        bucket_value = lane.capability_bucket or base.bucket.value
        try:
            bucket = CapabilityBucket(bucket_value)
        except ValueError:
            bucket = base.bucket
        max_reference_images = (
            lane.max_reference_images
            if lane.max_reference_images is not None
            else base.max_reference_images
        )
        return ProviderCapability(
            provider=lane.lane_id,
            bucket=bucket,
            supports_text_to_image=bucket in {
                CapabilityBucket.TEXT_ONLY,
                CapabilityBucket.SINGLE_REFERENCE,
                CapabilityBucket.MULTI_REFERENCE,
            },
            supports_image_to_image=bucket in {
                CapabilityBucket.SINGLE_REFERENCE,
                CapabilityBucket.MULTI_REFERENCE,
            },
            supports_multi_reference=bucket == CapabilityBucket.MULTI_REFERENCE,
            max_reference_images=max_reference_images,
            supported_sizes=list(base.supported_sizes),
            max_concurrency=lane.max_concurrency,
            enabled=lane.enabled and base.enabled and lane.degradation_status != "degraded_excluded",
        )

    adapter.capability = lane_capability  # type: ignore[method-assign]
    return adapter


def _build_lane_adapter(lane: LaneConfig) -> BaseAdapter:
    provider = lane.provider
    if provider == "vectorengine":
        return _apply_lane_identity(
            VectorEngineGeminiAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_VECTORENGINE_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_VECTORENGINE_MODEL", "gemini-3-pro-image-preview"),
                base_url=lane.base_url or os.environ.get("MIR_VECTORENGINE_BASE_URL", "https://api.vectorengine.ai"),
                extra_headers=_lane_extra_headers(lane, {"New-Api-User": "MIR_VECTORENGINE_API_USER"}),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "vectorengine_compat":
        if lane.route_path and "images/generations" in lane.route_path:
            return _apply_lane_identity(
                GptGeOpenAIImagesAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_VECTORENGINE_BASE_URL", "https://api.vectorengine.ai"),
                    timeout_seconds=_timeout_value(lane, "MIR_VECTORENGINE_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_VECTORENGINE_COMPAT_MODEL", "gemini-3.1-flash-image-preview"),
                    extra_headers=_lane_extra_headers(lane, {"New-Api-User": "MIR_VECTORENGINE_API_USER"}),
                    endpoint_path=lane.route_path,
                    image_contract=lane.image_contract,
                ),
                lane,
            )
        return _apply_lane_identity(
            VectorEngineOpenAICompatAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_VECTORENGINE_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_VECTORENGINE_COMPAT_MODEL", "gemini-3.1-flash-image-preview"),
                base_url=lane.base_url or os.environ.get("MIR_VECTORENGINE_BASE_URL", "https://api.vectorengine.ai"),
                extra_headers=_lane_extra_headers(lane, {"New-Api-User": "MIR_VECTORENGINE_API_USER"}),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "aifast":
        return _apply_lane_identity(
            AifastGeminiAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_AIFAST_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_AIFAST_MODEL", "gemini-3-pro-image-preview-vip"),
                base_url=lane.base_url or os.environ.get("MIR_AIFAST_BASE_URL", "https://chat.aifast.site"),
                extra_headers=_lane_extra_headers(lane, {"New-Api-User": "MIR_AIFAST_API_USER"}),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "aiwave":
        if lane.route_path and "images/generations" in lane.route_path:
            return _apply_lane_identity(
                GptGeOpenAIImagesAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_AIWAVE_BASE_URL", "https://www.ai-wave.org"),
                    timeout_seconds=_timeout_value(lane, "MIR_AIWAVE_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_AIWAVE_MODEL", "gemini-3-pro-image-preview"),
                    extra_headers=_lane_extra_headers(lane),
                    endpoint_path=lane.route_path,
                    preflight_timeout_seconds=_preflight_timeout_value(lane),
                ),
                lane,
            )
        return _apply_lane_identity(
            AiWaveGeminiAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_AIWAVE_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_AIWAVE_MODEL", "gemini-3-pro-image-preview"),
                base_url=lane.base_url or os.environ.get("MIR_AIWAVE_BASE_URL", "https://api2.ai-wave.org/gemini"),
                extra_headers=_lane_extra_headers(lane),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "aifast_compat":
        return _apply_lane_identity(
            AifastOpenAICompatAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_AIFAST_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_AIFAST_COMPAT_MODEL", "gemini-3-pro-image-preview-4k"),
                base_url=lane.base_url or os.environ.get("MIR_AIFAST_BASE_URL", "https://aifast.site"),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "gptge":
        return _apply_lane_identity(
            GptGeOpenAIImagesAdapter(
                api_key=os.environ[lane.api_key_env],
                base_url=lane.base_url or os.environ.get("MIR_GPTGE_BASE_URL", "https://api.gpt.ge"),
                timeout_seconds=_timeout_value(lane, "MIR_GPTGE_TIMEOUT", "180"),
                model_name=lane.model or os.environ.get("MIR_GPTGE_MODEL", "gemini-3-pro-image-preview"),
                extra_headers=_lane_extra_headers(lane, {"X-Api-User": "MIR_GPTGE_API_USER"}),
                endpoint_path=lane.route_path,
                image_contract=lane.image_contract,
            ),
            lane,
        )
    if provider == "laozhang":
        if lane.route_path and "generateContent" in lane.route_path:
            return _apply_lane_identity(
                ApiyiGeminiAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_LAOZHANG_BASE_URL", "https://api.laozhang.ai"),
                    timeout_seconds=_timeout_value(lane, "MIR_LAOZHANG_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_LAOZHANG_MODEL", "gemini-3-pro-image-preview"),
                    extra_headers=_lane_extra_headers(lane),
                    endpoint_path=lane.route_path,
                    preflight_timeout_seconds=_preflight_timeout_value(lane),
                ),
                lane,
            )
        return _apply_lane_identity(
            LaozhangOpenAIImagesAdapter(
                api_key=os.environ[lane.api_key_env],
                base_url=lane.base_url or os.environ.get("MIR_LAOZHANG_BASE_URL", "https://api.laozhang.ai"),
                timeout_seconds=_timeout_value(lane, "MIR_LAOZHANG_TIMEOUT", "180"),
                model_name=lane.model or os.environ.get("MIR_LAOZHANG_MODEL", "gemini-3-pro-image-preview"),
                extra_headers=_lane_extra_headers(lane),
                endpoint_path=lane.route_path,
                image_contract=lane.image_contract,
            ),
            lane,
        )
    if provider == "apiyi":
        if lane.route_path and "generateContent" in lane.route_path:
            return _apply_lane_identity(
                ApiyiGeminiAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_APIYI_BASE_URL", "https://api.apiyi.com"),
                    timeout_seconds=_timeout_value(lane, "MIR_APIYI_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_APIYI_MODEL", "gemini-3-pro-image-preview"),
                    extra_headers=_lane_extra_headers(lane),
                    endpoint_path=lane.route_path,
                    preflight_timeout_seconds=_preflight_timeout_value(lane),
                ),
                lane,
            )
        return _apply_lane_identity(
            ApiyiOpenAIImagesAdapter(
                api_key=os.environ[lane.api_key_env],
                base_url=lane.base_url or os.environ.get("MIR_APIYI_BASE_URL", "https://api.apiyi.com"),
                timeout_seconds=_timeout_value(lane, "MIR_APIYI_TIMEOUT", "180"),
                model_name=lane.model or os.environ.get("MIR_APIYI_MODEL", "gemini-3-pro-image-preview-4k"),
                extra_headers=_lane_extra_headers(lane),
                endpoint_path=lane.route_path,
                image_contract=lane.image_contract,
            ),
            lane,
        )
    if provider == "rightcodes":
        if lane.route_path and "generateContent" in lane.route_path:
            return _apply_lane_identity(
                VectorEngineGeminiAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_RIGHTCODES_GEMINI_BASE_URL", "https://right.codes/gemini"),
                    timeout_seconds=_timeout_value(lane, "MIR_RIGHTCODES_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_RIGHTCODES_GEMINI_MODEL", "gemini-3-pro-image-preview"),
                    extra_headers=_lane_extra_headers(lane),
                    endpoint_path=lane.route_path,
                ),
                lane,
            )
        if lane.route_path and "images/generations" in lane.route_path:
            return _apply_lane_identity(
                GptGeOpenAIImagesAdapter(
                    api_key=os.environ[lane.api_key_env],
                    base_url=lane.base_url or os.environ.get("MIR_RIGHTCODES_BASE_URL", "https://www.right.codes/draw"),
                    timeout_seconds=_timeout_value(lane, "MIR_RIGHTCODES_TIMEOUT", "300"),
                    model_name=lane.model or os.environ.get("MIR_RIGHTCODES_MODEL", "gpt-image-2"),
                    extra_headers=_lane_extra_headers(lane),
                    endpoint_path=lane.route_path,
                    image_contract=lane.image_contract,
                ),
                lane,
            )
        return _apply_lane_identity(
            VectorEngineOpenAICompatAdapter(
                api_key=os.environ[lane.api_key_env],
                timeout_seconds=_timeout_value(lane, "MIR_RIGHTCODES_TIMEOUT", "300"),
                model_name=lane.model or os.environ.get("MIR_RIGHTCODES_MODEL", "gemini-3-pro-image-preview"),
                base_url=lane.base_url or os.environ.get("MIR_RIGHTCODES_BASE_URL", "https://www.right.codes/draw"),
                extra_headers=_lane_extra_headers(lane),
                endpoint_path=lane.route_path,
            ),
            lane,
        )
    if provider == "apimart":
        return _apply_lane_identity(
            GptGeOpenAIImagesAdapter(
                api_key=os.environ[lane.api_key_env],
                base_url=lane.base_url or os.environ.get("MIR_APIMART_BASE_URL", "https://api.apimart.ai"),
                timeout_seconds=_timeout_value(lane, "MIR_APIMART_TIMEOUT", "180"),
                model_name=lane.model or os.environ.get("MIR_APIMART_MODEL", "gpt-image-2"),
                extra_headers=_lane_extra_headers(lane),
                endpoint_path=lane.route_path,
                image_contract=lane.image_contract,
            ),
            lane,
        )
    raise KeyError(f"Unknown lane provider: {lane.provider}")


def build_adapter(provider: str) -> BaseAdapter:
    provider = provider.strip().lower()
    lanes = load_lane_configs()
    if provider in lanes:
        return _build_lane_adapter(lanes[provider])
    if provider == "vectorengine":
        timeout_seconds = int(os.environ.get("MIR_VECTORENGINE_TIMEOUT", "300"))
        model_name = os.environ.get("MIR_VECTORENGINE_MODEL", "gemini-3-pro-image-preview")
        base_url = os.environ.get("MIR_VECTORENGINE_BASE_URL", "https://api.vectorengine.ai")
        return VectorEngineGeminiAdapter(
            api_key=os.environ["MIR_VECTORENGINE_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            base_url=base_url,
            extra_headers=_extra_headers_from_env({
                "New-Api-User": "MIR_VECTORENGINE_API_USER",
            }),
        )
    if provider in {"vectorengine_compat", "vectorengine-openai", "vectorengine-openai-compat"}:
        timeout_seconds = int(os.environ.get("MIR_VECTORENGINE_TIMEOUT", "300"))
        compat_model = os.environ.get("MIR_VECTORENGINE_COMPAT_MODEL", "gemini-3.1-flash-image-preview")
        base_url = os.environ.get("MIR_VECTORENGINE_BASE_URL", "https://api.vectorengine.ai")
        return VectorEngineOpenAICompatAdapter(
            api_key=os.environ["MIR_VECTORENGINE_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=compat_model,
            base_url=base_url,
            extra_headers=_extra_headers_from_env({
                "New-Api-User": "MIR_VECTORENGINE_API_USER",
            }),
        )
    if provider == "aifast":
        timeout_seconds = int(os.environ.get("MIR_AIFAST_TIMEOUT", "300"))
        model_name = os.environ.get("MIR_AIFAST_MODEL", "gemini-3-pro-image-preview-vip")
        base_url = os.environ.get("MIR_AIFAST_BASE_URL", "https://chat.aifast.site")
        return AifastGeminiAdapter(
            api_key=os.environ["MIR_AIFAST_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            base_url=base_url,
            extra_headers=_extra_headers_from_env({
                "New-Api-User": "MIR_AIFAST_API_USER",
            }),
        )
    if provider in {"aiwave", "ai-wave", "ai_wave"}:
        timeout_seconds = int(os.environ.get("MIR_AIWAVE_TIMEOUT", "300"))
        model_name = os.environ.get("MIR_AIWAVE_MODEL", "gemini-3-pro-image-preview")
        base_url = os.environ.get("MIR_AIWAVE_BASE_URL", "https://api2.ai-wave.org/gemini")
        return AiWaveGeminiAdapter(
            api_key=os.environ["MIR_AIWAVE_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            base_url=base_url,
        )
    if provider in {"aifast_compat", "aifast-openai", "aifast-openai-compat"}:
        timeout_seconds = int(os.environ.get("MIR_AIFAST_TIMEOUT", "300"))
        compat_model = os.environ.get("MIR_AIFAST_COMPAT_MODEL", "gemini-3-pro-image-preview-4k")
        base_url = os.environ.get("MIR_AIFAST_BASE_URL", "https://aifast.site")
        return AifastOpenAICompatAdapter(
            api_key=os.environ["MIR_AIFAST_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=compat_model,
            base_url=base_url,
        )
    if provider in {"gptge", "gpt.ge"}:
        timeout_seconds = int(os.environ.get("MIR_GPTGE_TIMEOUT", "180"))
        base_url = os.environ.get("MIR_GPTGE_BASE_URL", "https://api.gpt.ge")
        model_name = os.environ.get("MIR_GPTGE_MODEL", "gemini-3-pro-image-preview")
        return GptGeOpenAIImagesAdapter(
            api_key=os.environ["MIR_GPTGE_API_KEY"],
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            extra_headers=_extra_headers_from_env({
                "X-Api-User": "MIR_GPTGE_API_USER",
            }),
        )
    if provider in {"laozhang", "laozhang-api"}:
        timeout_seconds = int(os.environ.get("MIR_LAOZHANG_TIMEOUT", "180"))
        base_url = os.environ.get("MIR_LAOZHANG_BASE_URL", "https://api.laozhang.ai")
        model_name = os.environ.get("MIR_LAOZHANG_MODEL", "gemini-3-pro-image-preview")
        return LaozhangOpenAIImagesAdapter(
            api_key=os.environ["MIR_LAOZHANG_API_KEY"],
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
        )
    if provider in {"apiyi", "apiyi-api"}:
        timeout_seconds = int(os.environ.get("MIR_APIYI_TIMEOUT", "180"))
        base_url = os.environ.get("MIR_APIYI_BASE_URL", "https://api.apiyi.com")
        model_name = os.environ.get("MIR_APIYI_MODEL", "gemini-3-pro-image-preview-4k")
        return ApiyiOpenAIImagesAdapter(
            api_key=os.environ["MIR_APIYI_API_KEY"],
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
        )
    if provider in {"rightcodes", "right.codes", "right-codes"}:
        timeout_seconds = int(os.environ.get("MIR_RIGHTCODES_TIMEOUT", "300"))
        base_url = os.environ.get("MIR_RIGHTCODES_BASE_URL", "https://www.right.codes/draw")
        model_name = os.environ.get("MIR_RIGHTCODES_MODEL", "gemini-3-pro-image-preview")
        return VectorEngineOpenAICompatAdapter(
            api_key=os.environ["MIR_RIGHTCODES_API_KEY"],
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            base_url=base_url,
        )
    if provider in {"apimart", "api-mart"}:
        timeout_seconds = int(os.environ.get("MIR_APIMART_TIMEOUT", "180"))
        base_url = os.environ.get("MIR_APIMART_BASE_URL", "https://api.apimart.ai")
        model_name = os.environ.get("MIR_APIMART_MODEL", "gpt-image-2")
        return GptGeOpenAIImagesAdapter(
            api_key=os.environ["MIR_APIMART_API_KEY"],
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
        )
    raise KeyError(f"Unknown provider: {provider}")
