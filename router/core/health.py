from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from router.core.models import GenerationResult
from router.core.response_contract import response_contract_signal


class ProviderErrorCategory(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    UPSTREAM_5XX = "upstream_5xx"
    AUTH = "auth"
    FORBIDDEN = "forbidden"
    UNSUPPORTED = "unsupported"
    PARSE_ERROR = "parse_error"
    NO_IMAGE = "no_image"
    ASYNC_PENDING = "async_pending"
    INVALID_ARTIFACT = "invalid_artifact"
    CONNECTION = "connection"
    UNKNOWN = "unknown"


class RoutingAction(str, Enum):
    NOOP = "noop"
    USE_RESULT = "use_result"
    RETRY_SAME_PROVIDER = "retry_same_provider"
    FALLBACK_PROVIDER = "fallback_provider"
    HARD_STOP = "hard_stop"
    COOLDOWN_PROVIDER = "cooldown_provider"


@dataclass(slots=True)
class ProviderErrorDecision:
    category: ProviderErrorCategory
    action: RoutingAction
    retryable: bool
    cooldown_seconds: float = 0.0
    remediation_hint: str | None = None


def _combined_error_text(result: GenerationResult) -> str:
    return f"{result.error_code or ''} {result.error_message or ''}".lower()


def _provider_family(provider: str) -> str:
    normalized = provider.strip().lower()
    for family in ("vectorengine", "gptge", "aifast", "aiwave", "laozhang", "apiyi"):
        if family in normalized:
            return family
    return normalized


def _provider_remediation_hint(
    result: GenerationResult,
    category: ProviderErrorCategory,
    default_hint: str | None,
) -> str | None:
    family = _provider_family(result.provider)
    message = _combined_error_text(result)

    if category == ProviderErrorCategory.AUTH:
        if family == "gptge":
            return "gptge_auth_check_key_group_base_url_model_and_x_api_user"
        if family in {"laozhang", "apiyi"}:
            return f"{family}_auth_check_bearer_key_base_url_and_model_permission"
        if family in {"vectorengine", "aifast"}:
            return f"{family}_auth_check_key_group_new_api_user_header_and_model_permission"
        return default_hint

    if category == ProviderErrorCategory.FORBIDDEN:
        if family == "aiwave" and ("1010" in message or "cloudflare" in message):
            return "aiwave_cloudflare_or_ip_risk_control_check_route_and_access_policy"
        if family in {"vectorengine", "aifast"}:
            return f"{family}_forbidden_check_account_group_route_path_and_required_headers"
        return default_hint

    if category == ProviderErrorCategory.RATE_LIMIT:
        if "quota" in message or "balance" in message or "insufficient" in message:
            return f"{family}_quota_or_balance_exhausted_check_panel_before_retry"
        if family in {"vectorengine", "aifast", "aiwave"}:
            return f"{family}_rate_limited_check_account_queue_ip_risk_and_reduce_concurrency"
        return f"{family}_rate_limited_cooldown_and_try_independent_lane"

    if category == ProviderErrorCategory.TIMEOUT:
        if family == "vectorengine":
            return "vectorengine_timeout_check_base_url_route_path_adapter_family_payload_size_and_queue"
        if family in {"gptge", "laozhang", "apiyi"}:
            return f"{family}_timeout_check_openai_images_path_model_queue_and_payload_size"
        return f"{family}_timeout_check_route_path_payload_size_and_provider_queue"

    if category == ProviderErrorCategory.UNSUPPORTED:
        return f"{family}_unsupported_check_adapter_family_model_name_request_type_and_route_path"

    if category == ProviderErrorCategory.NO_IMAGE:
        return f"{family}_no_image_check_response_parser_content_filter_model_mode_and_request_body"

    if category == ProviderErrorCategory.ASYNC_PENDING:
        return f"{family}_async_pending_needs_polling_adapter_or_longer_queue_strategy"

    if category == ProviderErrorCategory.INVALID_ARTIFACT:
        return f"{family}_invalid_artifact_check_response_blob_encoding_parser_and_provider_quality"

    return default_hint


def _invalid_artifact(result: GenerationResult) -> bool:
    if str(result.error_code or "").lower() == "invalid_artifact":
        return True
    metrics = result.artifact_metrics or {}
    if not isinstance(metrics, dict):
        return False
    artifact_count = int(metrics.get("artifact_count") or 0)
    valid_image_count = int(metrics.get("valid_image_count") or 0)
    invalid_image_count = int(metrics.get("invalid_image_count") or 0)
    dimension_mismatch_count = int(metrics.get("dimension_mismatch_count") or 0)
    return bool(result.ok) and (
        artifact_count <= 0
        or valid_image_count <= 0
        or invalid_image_count > 0
        or dimension_mismatch_count > 0
    )


def _decision(
    result: GenerationResult,
    *,
    category: ProviderErrorCategory,
    action: RoutingAction,
    retryable: bool,
    cooldown_seconds: float = 0.0,
    remediation_hint: str | None = None,
) -> ProviderErrorDecision:
    return ProviderErrorDecision(
        category=category,
        action=action,
        retryable=retryable,
        cooldown_seconds=cooldown_seconds,
        remediation_hint=_provider_remediation_hint(result, category, remediation_hint),
    )


def classify_generation_failure(result: GenerationResult) -> ProviderErrorDecision:
    if _invalid_artifact(result):
        return _decision(
            result,
            category=ProviderErrorCategory.INVALID_ARTIFACT,
            action=RoutingAction.FALLBACK_PROVIDER,
            retryable=False,
            remediation_hint="invalid_artifact",
        )

    if result.ok:
        return ProviderErrorDecision(
            category=ProviderErrorCategory.SUCCESS,
            action=RoutingAction.USE_RESULT,
            retryable=False,
        )

    message = _combined_error_text(result)
    response_contract = response_contract_signal(result.provider_response)

    if response_contract.get("async_pending"):
        return _decision(
            result,
            category=ProviderErrorCategory.ASYNC_PENDING,
            action=RoutingAction.FALLBACK_PROVIDER,
            retryable=False,
            remediation_hint="async_pending_requires_polling",
        )

    if any(marker in message for marker in ("401", "unauthorized", "invalid api key", "invalid_api_key", "authentication")):
        return _decision(
            result,
            category=ProviderErrorCategory.AUTH,
            action=RoutingAction.HARD_STOP,
            retryable=False,
            remediation_hint="auth_failure",
        )

    if any(marker in message for marker in ("unsupported", "requires", "not supported", "400", "404", "not found", "invalid")):
        return _decision(
            result,
            category=ProviderErrorCategory.UNSUPPORTED,
            action=RoutingAction.HARD_STOP,
            retryable=False,
            remediation_hint="unsupported_request",
        )

    if any(marker in message for marker in ("429", "rate limit", "ratelimit", "too many requests", "quota")):
        return _decision(
            result,
            category=ProviderErrorCategory.RATE_LIMIT,
            action=RoutingAction.COOLDOWN_PROVIDER,
            retryable=True,
            cooldown_seconds=60.0,
            remediation_hint="rate_limit_or_quota",
        )

    if any(marker in message for marker in ("403", "forbidden", "permission denied", "cloudflare")):
        return _decision(
            result,
            category=ProviderErrorCategory.FORBIDDEN,
            action=RoutingAction.COOLDOWN_PROVIDER,
            retryable=True,
            cooldown_seconds=300.0,
            remediation_hint="route_or_account_forbidden",
        )

    if any(marker in message for marker in ("timeout", "timed out")):
        return _decision(
            result,
            category=ProviderErrorCategory.TIMEOUT,
            action=RoutingAction.RETRY_SAME_PROVIDER,
            retryable=True,
        )

    if any(marker in message for marker in ("500", "502", "503", "504", "service unavailable", "bad gateway")):
        return _decision(
            result,
            category=ProviderErrorCategory.UPSTREAM_5XX,
            action=RoutingAction.RETRY_SAME_PROVIDER,
            retryable=True,
        )

    if any(marker in message for marker in ("connection", "reset", "ssl", "network", "remote end closed")):
        return _decision(
            result,
            category=ProviderErrorCategory.CONNECTION,
            action=RoutingAction.RETRY_SAME_PROVIDER,
            retryable=True,
        )

    if any(marker in message for marker in ("parse", "jsondecode", "decode")):
        return _decision(
            result,
            category=ProviderErrorCategory.PARSE_ERROR,
            action=RoutingAction.RETRY_SAME_PROVIDER,
            retryable=True,
        )

    if "no_image" in message or "no image" in message:
        return _decision(
            result,
            category=ProviderErrorCategory.NO_IMAGE,
            action=RoutingAction.RETRY_SAME_PROVIDER,
            retryable=True,
        )

    return _decision(
        result,
        category=ProviderErrorCategory.UNKNOWN,
        action=RoutingAction.RETRY_SAME_PROVIDER,
        retryable=True,
    )


def should_retry_same_provider(decision: ProviderErrorDecision) -> bool:
    return decision.action == RoutingAction.RETRY_SAME_PROVIDER


def should_fallback_provider(decision: ProviderErrorDecision) -> bool:
    return decision.action in {
        RoutingAction.RETRY_SAME_PROVIDER,
        RoutingAction.FALLBACK_PROVIDER,
        RoutingAction.COOLDOWN_PROVIDER,
    }


def cooldown_seconds_for(decision: ProviderErrorDecision) -> float:
    return max(0.0, decision.cooldown_seconds)
