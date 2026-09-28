from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.perf_report import build_performance_report


class PerfReportTests(unittest.TestCase):
    def test_report_includes_configured_keyed_lanes_without_history(self) -> None:
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
accounts:
  pro-account:
    station: pro-main
  pro-backup-account:
    station: pro-main
credentials:
  pro-credential:
    account: pro-account
    secretRef:
      type: env
      name: MIR_TEST_PRO_KEY
    authScheme: bearer
  pro-backup-credential:
    account: pro-backup-account
    secretRef:
      type: env
      name: MIR_TEST_PRO_BACKUP_KEY
    authScheme: bearer
routes:
  pro-images:
    station: pro-main
    adapterFamily: openai_images
    path: /v1/images/generations
  pro-images-backup:
    station: pro-main
    adapterFamily: openai_images
    path: /v1/images/generations
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
  pro-images-backup-lane:
    provider: laozhang
    providerGroup: pro-group
    sourceTier: professional
    station: pro-main
    account: pro-backup-account
    credential: pro-backup-credential
    route: pro-images-backup
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 1
    priority: 20
    enabled: true
""", encoding="utf-8")
            perf_log = root / "performance.jsonl"
            perf_log.write_text(json.dumps({
                "event": "generation_result",
                "provider": "fast-logged-lane",
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
                    "one": {
                        "attempts": [
                            {
                                "provider": "pro-images-lane",
                                "provider_group": "pro-group",
                                "pool_identity": "pro-group/pro-main/pro-account",
                                "ok": True,
                                "latency_ms": 18000,
                                "artifact_metrics": {
                                    "artifact_count": 1,
                                    "valid_image_count": 1,
                                    "invalid_image_count": 0,
                                },
                            },
                        ],
                    },
                },
            }) + "\n", encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_TEST_PRO_KEY": "redaction-sentinel-not-in-report",
                "MIR_TEST_PRO_BACKUP_KEY": "redaction-backup-sentinel-not-in-report",
            }, clear=False):
                report = build_performance_report([str(perf_log)])

        serialized = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("redaction-sentinel-not-in-report", serialized)
        self.assertNotIn("redaction-backup-sentinel-not-in-report", serialized)
        self.assertEqual(report["configured_candidate_order"], ["pro-images-lane", "pro-images-backup-lane"])
        self.assertEqual(report["configured_candidates"][0]["pool_identity"], "pro-group/pro-main/pro-account")
        self.assertEqual(report["configured_candidates"][0]["station_id"], "pro-main")
        self.assertEqual(report["configured_candidates"][0]["account_id"], "pro-account")
        self.assertEqual(report["configured_candidates"][0]["credential_id"], "pro-credential")
        self.assertEqual(report["configured_candidates"][0]["route_id"], "pro-images")

        rows = {row["provider"]: row for row in report["weighted_lane_recommendations"]}
        self.assertEqual(rows["fast-logged-lane"]["reason"], "high_success_low_latency")
        self.assertEqual(rows["pro-images-lane"]["reason"], "unknown_or_stale_kept_for_recovery")
        self.assertEqual(rows["pro-images-backup-lane"]["reason"], "unknown_or_stale_kept_for_recovery")
        self.assertIn("pro-images-lane", report["weighted_lane_slots"])
        pool_rows = {row["pool_identity"]: row for row in report["pool_scores"]}
        self.assertEqual(pool_rows["pro-group/pro-main/pro-account"]["success_count"], 1)
        self.assertEqual(pool_rows["pro-group/pro-main/pro-account"]["artifact_count"], 1)
        self.assertEqual(pool_rows["pro-group/pro-main/pro-account"]["valid_image_count"], 1)
        self.assertEqual(pool_rows["pro-group/pro-main/pro-account"]["invalid_image_count"], 0)
        self.assertEqual(pool_rows["pro-group/pro-main/pro-account"]["status"], "ready_recent_success")
        self.assertEqual(pool_rows["pro-group/pro-main/pro-backup-account"]["status"], "unproven")
        self.assertEqual(report["provider_scores"][0]["valid_image_count"], 1)
        self.assertEqual(report["provider_scores"][0]["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(report["weighted_lane_recommendations"][0]["valid_image_count"], 1)
        self.assertEqual(report["weighted_lane_recommendations"][0]["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(report["pool_status_counts"], {"ready_recent_success": 1, "unproven": 1})


if __name__ == "__main__":
    unittest.main()
