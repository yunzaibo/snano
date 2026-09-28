from __future__ import annotations

import unittest

from router.core.recovery import recovery_event_from_attempt


class RecoveryEventTests(unittest.TestCase):
    def test_rate_limit_or_quota_event_requires_operator_quota_check(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "error_category": "rate_limit",
            "error_message": "HTTP Error 429 quota exceeded",
            "routing_action": "cooldown_provider",
        })

        self.assertEqual(event["action"], "operator_check_quota")
        self.assertEqual(event["risk"], "external_account")
        self.assertFalse(event["auto_execute"])

    def test_auth_event_requires_key_fix(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "error_category": "auth",
            "error_message": "401 unauthorized",
            "routing_action": "hard_stop",
        })

        self.assertEqual(event["action"], "rotate_or_fix_key")
        self.assertFalse(event["auto_execute"])

    def test_timeout_event_allows_passive_retry_or_fallback(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "error_category": "timeout",
            "error_message": "timed out",
            "routing_action": "retry_same_provider",
        })

        self.assertEqual(event["action"], "passive_retry_or_fallback")
        self.assertTrue(event["auto_execute"])

    def test_async_pending_event_requires_polling_adapter_work(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "pool_identity": "group/station/account",
            "error_category": "async_pending",
            "routing_action": "fallback_provider",
        })

        self.assertEqual(event["action"], "implement_or_enable_polling_adapter")
        self.assertEqual(event["risk"], "adapter_contract")
        self.assertEqual(event["pool_identity"], "group/station/account")
        self.assertFalse(event["auto_execute"])

    def test_invalid_artifact_event_requires_parser_or_quality_check(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "error_category": "invalid_artifact",
            "routing_action": "fallback_provider",
            "artifact_metrics": {
                "artifact_count": 1,
                "valid_image_count": 0,
                "invalid_image_count": 1,
            },
        })

        self.assertEqual(event["action"], "inspect_response_blob_parser_or_provider_quality")
        self.assertEqual(event["risk"], "output_quality")
        self.assertFalse(event["auto_execute"])

    def test_aspect_ratio_mismatch_success_event_requires_contract_review(self) -> None:
        event = recovery_event_from_attempt({
            "provider": "p1",
            "ok": True,
            "artifact_metrics": {
                "artifact_count": 1,
                "valid_image_count": 1,
                "invalid_image_count": 0,
                "aspect_ratio_mismatch_count": 1,
            },
        })

        self.assertEqual(event["error_category"], "aspect_ratio_mismatch")
        self.assertEqual(event["action"], "review_provider_size_aspect_contract")
        self.assertEqual(event["risk"], "output_quality")
        self.assertEqual(event["aspect_ratio_mismatch_count"], 1)
        self.assertFalse(event["auto_execute"])


if __name__ == "__main__":
    unittest.main()
