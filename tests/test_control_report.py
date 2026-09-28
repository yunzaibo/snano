from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from router.core.control_report import CONTROL_REPORT_SCHEMA_VERSION, build_control_report
from router.core.queue_store import QueueStore
from router.core.remediation_events import remediation_event_from_attempt


class ControlReportTests(unittest.TestCase):
    def test_control_report_is_local_secret_safe_and_agent_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_log = root / "performance.jsonl"
            queue_dir = root / "queue"
            job_file = root / "job.json"
            job_file.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{
                    "id": "one",
                    "requestType": "text_to_image",
                    "prompt": "tiny smoke image",
                }],
            }), encoding="utf-8")
            QueueStore(queue_dir).submit_task(str(job_file), preset="batch_draft", queue_priority=10)

            remediation_event = remediation_event_from_attempt({
                "provider": "p1",
                "error_category": "connection",
                "error_message": "connection reset",
            }, now=123.0)
            perf_log.write_text(json.dumps({
                "event": "batch_summary",
                "recorded_at": "2026-06-03T00:00:00Z",
                "schema_version": "batch-summary/v1",
                "success_count": 0,
                "failure_count": 1,
                "items_total": 1,
                "remediation_events": [remediation_event],
                "remediation_summary": {
                    "event_count": 1,
                },
            }, ensure_ascii=False) + "\n", encoding="utf-8")

            report = build_control_report(
                perf_log_path=str(perf_log),
                provider_state_path=str(root / "missing-state.json"),
                queue_dir=str(queue_dir),
                recent_batch_limit=2,
            )

        self.assertEqual(report["schema_version"], CONTROL_REPORT_SCHEMA_VERSION)
        self.assertEqual(report["network_calls"], 0)
        self.assertFalse(report["secret_values_included"])
        self.assertIn("readiness", report)
        self.assertEqual(report["queue"]["status_counts"]["pending"], 1)
        self.assertEqual(report["recent_batch_summaries"][0]["failure_count"], 1)
        self.assertGreaterEqual(report["remediation_summary"]["event_count"], 1)
        self.assertTrue(report["recommended_next_steps"])

    def test_control_report_does_not_create_missing_queue_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "missing-queue"

            report = build_control_report(
                perf_log_path=str(root / "missing-performance.jsonl"),
                provider_state_path=str(root / "missing-state.json"),
                queue_dir=str(queue_dir),
            )

            self.assertFalse(queue_dir.exists())

        self.assertFalse(report["queue"]["enabled"])
        self.assertTrue(report["queue"]["queue_missing"])
        self.assertEqual(report["queue"]["status_counts"], {})
        self.assertEqual(report["queue"]["tasks"], [])
        self.assertIsNone(report["queue"]["task"])


if __name__ == "__main__":
    unittest.main()
