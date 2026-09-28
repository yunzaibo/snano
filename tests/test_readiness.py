from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.readiness import build_readiness_report


class ReadinessTests(unittest.TestCase):
    def test_readiness_report_is_secret_safe_and_uses_perf_and_health(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  pro-main:
    kind: relay
    baseUrl: https://api.example.invalid
    vendorGroup: pro-group
    sourceTier: professional
    enabled: true
accounts:
  pro-account:
    station: pro-main
    enabled: true
credentials:
  pro-credential:
    account: pro-account
    secretRef:
      type: env
      name: MIR_TEST_PRO_KEY
    authScheme: bearer
    enabled: true
routes:
  pro-images:
    station: pro-main
    adapterFamily: openai_images
    path: /v1/images/generations
    enabled: true
lanes:
  pro-images-lane:
    provider: laozhang
    providerGroup: pro-group
    sourceTier: professional
    station: pro-main
    account: pro-account
    credential: pro-credential
    route: pro-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 1
    priority: 10
    enabled: true
""", encoding="utf-8")
            perf_log = root / "performance.jsonl"
            perf_log.write_text(json.dumps({
                "event": "generation_result",
                "provider": "pro-images-lane",
                "ok": True,
                "latency_ms": 12000,
                "artifact_metrics": {
                    "artifact_count": 1,
                    "valid_image_count": 1,
                    "invalid_image_count": 0,
                    "aspect_ratio_mismatch_count": 1,
                },
            }) + "\n" + json.dumps({
                "event": "batch_summary",
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "pro-images-lane-a",
                                "provider_group": "pro-group",
                                "pool_identity": "pro-group/pro-main/pro-account",
                                "ok": False,
                                "error_category": "rate_limit",
                                "artifact_metrics": {
                                    "artifact_count": 0,
                                    "valid_image_count": 0,
                                    "invalid_image_count": 0,
                                },
                            },
                            {
                                "provider": "pro-images-lane-b",
                                "provider_group": "pro-group",
                                "pool_identity": "pro-group/pro-main/pro-account",
                                "ok": False,
                                "error_category": "rate_limit",
                                "artifact_metrics": {
                                    "artifact_count": 1,
                                    "valid_image_count": 0,
                                    "invalid_image_count": 1,
                                },
                            },
                        ],
                    },
                },
            }) + "\n", encoding="utf-8")
            state_path = root / "provider-health.json"
            state_path.write_text(json.dumps({
                "schema_version": "provider-health/v1",
                "providers": {
                    "pro-images-lane": {
                        "healthy": True,
                        "cooldown_until": 0,
                        "weight": 1,
                        "consecutive_failures": 0,
                    },
                    "gptge": {
                        "healthy": True,
                        "cooldown_until": time.time() + 120,
                        "weight": 1,
                        "cooldown_reason": "rate_limit",
                        "last_error_category": "rate_limit",
                        "consecutive_failures": 2,
                    },
                },
            }), encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_TEST_PRO_KEY": "super-secret-lane-key",
                "MIR_GPTGE_API_KEY": "legacy-secret-key",
            }, clear=False):
                report = build_readiness_report(
                    perf_log_path=str(perf_log),
                    provider_state_path=str(state_path),
                    providers=["pro-images-lane", "gptge", "apiyi"],
                )

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("super-secret-lane-key", serialized)
        self.assertNotIn("legacy-secret-key", serialized)
        self.assertFalse(report["secret_values_included"])
        self.assertEqual(report["network_calls"], 0)
        self.assertIn("independent", report["default_pool_assumption"])
        self.assertEqual(report["pool_correlation_signals"][0]["provider_group"], "pro-group")

        rows = {row["id"]: row for row in report["lanes"]}
        self.assertEqual(rows["pro-images-lane"]["status"], "ready_recent_success")
        self.assertEqual(rows["pro-images-lane"]["source_tier"], "professional")
        self.assertEqual(rows["pro-images-lane"]["station_id"], "pro-main")
        self.assertEqual(rows["pro-images-lane"]["account_id"], "pro-account")
        self.assertEqual(rows["pro-images-lane"]["credential_id"], "pro-credential")
        self.assertEqual(rows["pro-images-lane"]["route_id"], "pro-images")
        self.assertEqual(rows["pro-images-lane"]["pool_identity"], "pro-group/pro-main/pro-account")
        self.assertEqual(rows["pro-images-lane"]["performance"]["success_count"], 1)
        self.assertEqual(rows["pro-images-lane"]["performance"]["valid_image_count"], 1)
        self.assertEqual(rows["pro-images-lane"]["performance"]["invalid_image_count"], 0)
        self.assertEqual(rows["pro-images-lane"]["performance"]["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(rows["gptge"]["status"], "cooldown")
        self.assertEqual(rows["apiyi"]["status"], "missing_credential")

        pools = {row["pool_identity"]: row for row in report["pools"]}
        self.assertEqual(pools["pro-group/pro-main/pro-account"]["status"], "pool_risk_no_recent_success")
        self.assertEqual(pools["pro-group/pro-main/pro-account"]["performance"]["attempt_count"], 2)
        self.assertEqual(
            pools["pro-group/pro-main/pro-account"]["performance"]["errors"],
            {"rate_limit": 2},
        )
        self.assertEqual(pools["pro-group/pro-main/pro-account"]["performance"]["artifact_count"], 1)
        self.assertEqual(pools["pro-group/pro-main/pro-account"]["performance"]["invalid_image_count"], 1)
        self.assertEqual(report["pool_status_counts"]["pool_risk_no_recent_success"], 1)
        self.assertEqual(report["pool_status_counts"]["unproven"], 2)

    def test_readiness_warns_when_only_subsecond_success_samples_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_log = root / "performance.jsonl"
            perf_log.write_text(json.dumps({
                "event": "generation_result",
                "provider": "apiyi",
                "ok": True,
                "latency_ms": 10,
            }) + "\n", encoding="utf-8")

            with patch.dict(os.environ, {"MIR_APIYI_API_KEY": "secret"}, clear=False):
                report = build_readiness_report(
                    perf_log_path=str(perf_log),
                    providers=["apiyi"],
                    latency_budget_seconds=30,
                )

        row = report["lanes"][0]
        self.assertEqual(row["performance"]["success_count"], 0)
        self.assertEqual(row["performance"]["ignored_fast_success_count"], 1)
        self.assertGreater(report["performance_warning_count"], 0)
        self.assertTrue(any("真实成功样本不足" in warning for warning in row["performance_warnings"]))
        self.assertTrue(any("疑似测试或模拟数据" in warning for warning in row["performance_warnings"]))


if __name__ == "__main__":
    unittest.main()
