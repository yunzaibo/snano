from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from router.main import main


class QueueCliTests(unittest.TestCase):
    def test_submit_outputs_task_id_and_status_reads_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text(json.dumps({"jobType": "manifest", "items": [{"id": "one"}]}), encoding="utf-8")

            submitted = self._run_cli([
                "router/main.py",
                "submit",
                str(job),
                "--preset",
                "batch_draft",
                "--queue-dir",
                str(queue_dir),
            ])
            task_id = submitted["task_id"]

            status = self._run_cli(["router/main.py", "status", task_id, "--queue-dir", str(queue_dir)])

            self.assertTrue(submitted["ok"])
            self.assertEqual(status["task"]["task_id"], task_id)
            self.assertEqual(status["task"]["status"], "pending")

    def test_submit_accepts_speed_scheduling_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")

            submitted = self._run_cli([
                "router/main.py",
                "submit",
                str(job),
                "--queue-dir",
                str(queue_dir),
                "--queue-priority",
                "10",
                "--model-class",
                "Snano",
                "--station-tier",
                "professional_station",
                "--latency-budget-seconds",
                "30",
            ])

            task = submitted["task"]
            self.assertEqual(task["queue_priority"], 10)
            self.assertEqual(task["model_class"], "Snano")
            self.assertEqual(task["station_tier"], "professional_station")
            self.assertEqual(task["latency_budget_seconds"], 30)
            self.assertEqual(task["scheduling_policy"], "speed_first_capacity_aware")

    def test_cancel_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            submitted = self._run_cli(["router/main.py", "submit", str(job), "--queue-dir", str(queue_dir)])

            cancelled = self._run_cli(["router/main.py", "cancel", submitted["task_id"], "--queue-dir", str(queue_dir)])

            self.assertTrue(cancelled["ok"])
            self.assertEqual(cancelled["task"]["status"], "cancelled")

    def test_worker_once_outputs_completed_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            self._run_cli(["router/main.py", "submit", str(job), "--queue-dir", str(queue_dir)])
            fake_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )

            with patch("router.core.queue_worker.run_job_file", return_value=fake_result):
                result = self._run_cli([
                    "router/main.py",
                    "worker",
                    "--once",
                    "--queue-dir",
                    str(queue_dir),
                    "--provider-lock-dir",
                    str(root / "provider-locks"),
                ])

            self.assertTrue(result["ok"])
            self.assertEqual(result["task"]["status"], "done")

    def test_list_queue_includes_scheduler_wait_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            submitted = self._run_cli(["router/main.py", "submit", str(job), "--queue-dir", str(queue_dir)])
            from router.core.queue_store import QueueStore
            QueueStore(queue_dir).update_pending_scheduling(
                submitted["task_id"],
                scheduler_decision="waiting",
                wait_reason="provider_capacity_full",
                provider_waits=[{"provider": "p1", "wait_reason": "provider_capacity_full"}],
            )

            listed = self._run_cli(["router/main.py", "list-queue", "--queue-dir", str(queue_dir)])

            self.assertEqual(listed["tasks"][0]["wait_reason"], "provider_capacity_full")
            self.assertEqual(listed["tasks"][0]["provider_waits"][0]["provider"], "p1")

    def test_status_includes_effective_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            submitted = self._run_cli(["router/main.py", "submit", str(job), "--queue-dir", str(queue_dir)])
            from router.core.queue_store import QueueStore
            QueueStore(queue_dir).update_pending_scheduling(
                submitted["task_id"],
                scheduler_decision="waiting",
                selected_reason="fallback_provider_available",
                effective_providers=["p2", "p1"],
                provider_waits=[{"provider": "p1", "wait_reason": "provider_capacity_full"}],
            )

            status = self._run_cli(["router/main.py", "status", submitted["task_id"], "--queue-dir", str(queue_dir)])

            self.assertEqual(status["task"]["selected_reason"], "fallback_provider_available")
            self.assertEqual(status["task"]["effective_providers"], ["p2", "p1"])

    def _run_cli(self, argv: list[str]) -> dict:
        old_argv = sys.argv
        out = io.StringIO()
        try:
            sys.argv = argv
            with contextlib.redirect_stdout(out):
                main()
        finally:
            sys.argv = old_argv
        return json.loads(out.getvalue())


if __name__ == "__main__":
    unittest.main()
