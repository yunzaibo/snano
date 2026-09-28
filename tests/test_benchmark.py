from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from router.core.benchmark import BenchmarkConfig, benchmark_perf_log_path, run_internal_benchmark


class BenchmarkTests(unittest.TestCase):
    def test_default_benchmark_perf_log_is_isolated_from_performance_log(self) -> None:
        path = benchmark_perf_log_path()

        self.assertIn("/logs/internal-benchmark-", path)
        self.assertTrue(path.endswith(".jsonl"))
        self.assertNotIn("performance.jsonl", path)

    def test_run_internal_benchmark_builds_single_provider_items(self) -> None:
        captured: dict[str, object] = {}

        def fake_run_job_file(path: str, **kwargs: object) -> object:
            captured["kwargs"] = kwargs
            with open(path, encoding="utf-8") as fh:
                captured["job"] = json.loads(fh.read())
            item_results = [
                SimpleNamespace(
                    item_id="p1-sample-1",
                    status="success",
                    selected_provider="p1",
                    latency_ms=12000,
                    error_code=None,
                    error_message=None,
                    artifact_paths=["/tmp/image.png"],
                )
            ]
            return SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path="/tmp/run-summary.json",
                ledger_path="/tmp/ledger.json",
                item_results=item_results,
            )

        with patch("router.core.benchmark.run_job_file", side_effect=fake_run_job_file):
            result = run_internal_benchmark(BenchmarkConfig(
                providers=["p1", "p2"],
                samples=5,
                perf_log_file="/tmp/internal-benchmark-test.jsonl",
            ))

        job = captured["job"]
        kwargs = captured["kwargs"]
        self.assertEqual(result["perf_log_path"], "/tmp/internal-benchmark-test.jsonl")
        self.assertEqual(result["samples_per_provider"], 3)
        self.assertEqual(kwargs["perf_log_path"], "/tmp/internal-benchmark-test.jsonl")
        self.assertEqual(kwargs["max_workers"], 1)
        self.assertEqual(kwargs["max_retries_per_provider"], 0)
        self.assertEqual(len(job["items"]), 6)
        self.assertEqual(job["items"][0]["providers"], ["p1"])
        self.assertEqual(job["items"][3]["providers"], ["p2"])
        self.assertEqual(job["items"][0]["metadata"]["source"], "internal-benchmark")


if __name__ == "__main__":
    unittest.main()
