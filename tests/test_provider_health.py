from __future__ import annotations

import unittest

from router.core.health import (
    ProviderErrorCategory,
    RoutingAction,
    classify_generation_failure,
)
from router.core.models import GenerationResult


def failed_result(error_code: str, error_message: str, provider: str = "fake") -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code=error_code,
        error_message=error_message,
    )


def pending_result(provider: str = "fake") -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code="no_image",
        error_message="No image data found in provider response.",
        provider_response={
            "status": "queued",
            "task_id": "task-id-must-not-be-logged",
        },
    )


def invalid_artifact_result(provider: str = "fake") -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_paths=["/tmp/not-real.png"],
        artifact_metrics={
            "artifact_count": 1,
            "valid_image_count": 0,
            "invalid_image_count": 1,
        },
    )


class ProviderHealthClassifierTests(unittest.TestCase):
    def test_rate_limit_recommends_cooldown(self) -> None:
        decision = classify_generation_failure(failed_result("HTTPError", "HTTP Error 429: Too Many Requests"))

        self.assertEqual(decision.category, ProviderErrorCategory.RATE_LIMIT)
        self.assertEqual(decision.action, RoutingAction.COOLDOWN_PROVIDER)
        self.assertGreater(decision.cooldown_seconds, 0)

    def test_timeout_retries_same_provider_then_allows_fallback(self) -> None:
        decision = classify_generation_failure(failed_result("TimeoutError", "The request timed out"))

        self.assertEqual(decision.category, ProviderErrorCategory.TIMEOUT)
        self.assertEqual(decision.action, RoutingAction.RETRY_SAME_PROVIDER)
        self.assertTrue(decision.retryable)

    def test_upstream_5xx_is_retryable(self) -> None:
        decision = classify_generation_failure(failed_result("HTTPError", "HTTP Error 503: Service Unavailable"))

        self.assertEqual(decision.category, ProviderErrorCategory.UPSTREAM_5XX)
        self.assertTrue(decision.retryable)

    def test_auth_error_is_hard_stop(self) -> None:
        decision = classify_generation_failure(failed_result("HTTPError", "HTTP Error 401: Unauthorized"))

        self.assertEqual(decision.category, ProviderErrorCategory.AUTH)
        self.assertEqual(decision.action, RoutingAction.HARD_STOP)
        self.assertFalse(decision.retryable)

    def test_unsupported_request_is_hard_stop(self) -> None:
        decision = classify_generation_failure(failed_result("ValueError", "unsupported request type"))

        self.assertEqual(decision.category, ProviderErrorCategory.UNSUPPORTED)
        self.assertEqual(decision.action, RoutingAction.HARD_STOP)

    def test_no_image_is_retryable(self) -> None:
        decision = classify_generation_failure(failed_result("no_image", "No image data found in provider response."))

        self.assertEqual(decision.category, ProviderErrorCategory.NO_IMAGE)
        self.assertEqual(decision.action, RoutingAction.RETRY_SAME_PROVIDER)

    def test_async_pending_response_falls_back_without_same_provider_retry(self) -> None:
        decision = classify_generation_failure(pending_result(provider="gptge-key3-main-openai-images"))

        self.assertEqual(decision.category, ProviderErrorCategory.ASYNC_PENDING)
        self.assertEqual(decision.action, RoutingAction.FALLBACK_PROVIDER)
        self.assertFalse(decision.retryable)
        self.assertEqual(
            decision.remediation_hint,
            "gptge_async_pending_needs_polling_adapter_or_longer_queue_strategy",
        )

    def test_invalid_artifact_falls_back_without_counting_as_success(self) -> None:
        decision = classify_generation_failure(invalid_artifact_result(provider="aiwave"))

        self.assertEqual(decision.category, ProviderErrorCategory.INVALID_ARTIFACT)
        self.assertEqual(decision.action, RoutingAction.FALLBACK_PROVIDER)
        self.assertFalse(decision.retryable)
        self.assertEqual(
            decision.remediation_hint,
            "aiwave_invalid_artifact_check_response_blob_encoding_parser_and_provider_quality",
        )

    def test_unknown_error_is_retryable(self) -> None:
        decision = classify_generation_failure(failed_result("RuntimeError", "unexpected provider failure"))

        self.assertEqual(decision.category, ProviderErrorCategory.UNKNOWN)
        self.assertTrue(decision.retryable)

    def test_gptge_auth_error_has_provider_specific_hint(self) -> None:
        decision = classify_generation_failure(
            failed_result("HTTPError", "HTTP Error 401: Unauthorized", provider="gptge-key3-main-openai-images")
        )

        self.assertEqual(decision.category, ProviderErrorCategory.AUTH)
        self.assertEqual(decision.remediation_hint, "gptge_auth_check_key_group_base_url_model_and_x_api_user")

    def test_aiwave_cloudflare_error_has_provider_specific_hint(self) -> None:
        decision = classify_generation_failure(
            failed_result("HTTPError", "HTTP Error 403: Cloudflare error code 1010", provider="aiwave")
        )

        self.assertEqual(decision.category, ProviderErrorCategory.FORBIDDEN)
        self.assertEqual(decision.remediation_hint, "aiwave_cloudflare_or_ip_risk_control_check_route_and_access_policy")

    def test_vectorengine_timeout_hint_points_to_route_and_adapter_contract(self) -> None:
        decision = classify_generation_failure(
            failed_result("TimeoutError", "The request timed out", provider="vectorengine_compat")
        )

        self.assertEqual(decision.category, ProviderErrorCategory.TIMEOUT)
        self.assertEqual(
            decision.remediation_hint,
            "vectorengine_timeout_check_base_url_route_path_adapter_family_payload_size_and_queue",
        )


if __name__ == "__main__":
    unittest.main()
