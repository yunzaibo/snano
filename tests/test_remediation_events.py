from __future__ import annotations

import json
import unittest

from router.core.remediation_events import (
    REMEDIATION_EVENT_SCHEMA_VERSION,
    remediation_event_from_attempt,
    remediation_event_from_readiness_lane,
    remediation_events_from_items,
    remediation_summary,
)


class RemediationEventTests(unittest.TestCase):
    def test_auth_event_requires_approval_and_redacts_secret_like_text(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "apiyi",
            "provider_group": "apiyi-group",
            "error_category": "auth",
            "error_message": "401 invalid api_key=abc123456789",
            "routing_action": "hard_stop",
        }, now=123.0)

        self.assertIsNotNone(event)
        assert event is not None
        payload = json.dumps(event, ensure_ascii=False)
        self.assertEqual(event["schema_version"], REMEDIATION_EVENT_SCHEMA_VERSION)
        self.assertEqual(event["event_type"], "credential")
        self.assertEqual(event["safety_class"], "secret_or_account")
        self.assertTrue(event["approval_required"])
        self.assertFalse(event["auto_execute"])
        self.assertFalse(event["secret_values_included"])
        self.assertNotIn("abc123456789", payload)

    def test_quota_rate_limit_requires_operator_approval(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "rightcodes",
            "error_category": "rate_limit",
            "error_message": "HTTP 429 quota balance insufficient",
        })

        assert event is not None
        self.assertEqual(event["event_type"], "quota_or_balance")
        self.assertEqual(event["safety_class"], "external_spend")
        self.assertTrue(event["approval_required"])
        self.assertFalse(event["auto_execute"])

    def test_plain_rate_limit_can_be_handled_locally(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "laozhang",
            "error_category": "rate_limit",
            "error_message": "too many requests",
        })

        assert event is not None
        self.assertEqual(event["event_type"], "rate_limit")
        self.assertEqual(event["recommended_action"], "cooldown_or_reduce_concurrency")
        self.assertFalse(event["approval_required"])
        self.assertTrue(event["auto_execute"])

    def test_connection_failure_is_provider_outage(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "gptge",
            "error_category": "connection",
            "error_message": "connection reset",
        })

        assert event is not None
        self.assertEqual(event["event_type"], "provider_outage")
        self.assertEqual(event["safety_class"], "local_safe")
        self.assertTrue(event["auto_execute"])

    def test_model_channel_unavailable_is_local_config_event(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "apiyi-simage-gpt-image-2",
            "provider_group": "apiyi-group",
            "model": "gpt-image-2",
            "error_category": "upstream_5xx",
            "error_message": "Current group has no available channels for model gpt-image-2 under billing mode pay-per-request",
        })

        assert event is not None
        self.assertEqual(event["event_type"], "model_or_channel_unavailable")
        self.assertEqual(event["recommended_action"], "change_model_alias_account_or_channel")
        self.assertEqual(event["safety_class"], "local_config")
        self.assertTrue(event["approval_required"])
        self.assertFalse(event["auto_execute"])

    def test_success_without_artifact_mismatch_does_not_emit_event(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "apiyi",
            "ok": True,
            "artifact_metrics": {
                "aspect_ratio_mismatch_count": 0,
            },
        })

        self.assertIsNone(event)

    def test_success_with_artifact_mismatch_emits_adapter_drift_event(self) -> None:
        event = remediation_event_from_attempt({
            "provider": "apiyi",
            "ok": True,
            "artifact_metrics": {
                "aspect_ratio_mismatch_count": 2,
            },
        })

        assert event is not None
        self.assertEqual(event["error_category"], "aspect_ratio_mismatch")
        self.assertEqual(event["event_type"], "adapter_drift")
        self.assertEqual(event["aspect_ratio_mismatch_count"], 2)

    def test_items_event_builder_dedupes_same_remediation(self) -> None:
        items = [{
            "attempts": [
                {"provider": "apiyi", "error_category": "timeout"},
                {"provider": "apiyi", "error_category": "timeout"},
            ],
            "skipped_providers": [
                {"provider": "apiyi", "error_category": "timeout", "reason": "cooldown"},
            ],
        }]

        events = remediation_events_from_items(items, now=123.0)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "provider_outage")

    def test_readiness_missing_credential_emits_credential_event(self) -> None:
        event = remediation_event_from_readiness_lane({
            "id": "apimart",
            "status": "missing_credential",
            "provider_group": "apimart-group",
            "credential_present": False,
            "health": {},
        }, now=123.0)

        assert event is not None
        self.assertEqual(event["source"], "readiness")
        self.assertEqual(event["event_type"], "credential")
        self.assertTrue(event["approval_required"])

    def test_summary_counts_safety_and_approval(self) -> None:
        events = [
            remediation_event_from_attempt({"provider": "p1", "error_category": "auth"}),
            remediation_event_from_attempt({"provider": "p2", "error_category": "connection"}),
        ]

        summary = remediation_summary([event for event in events if event])

        self.assertEqual(summary["event_count"], 2)
        self.assertEqual(summary["approval_required_count"], 1)
        self.assertEqual(summary["auto_executable_count"], 1)
        self.assertEqual(summary["by_safety_class"]["secret_or_account"], 1)
        self.assertEqual(summary["by_safety_class"]["local_safe"], 1)


if __name__ == "__main__":
    unittest.main()
