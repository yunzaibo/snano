from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.queue_store import QUEUE_TASK_SCHEMA_VERSION, QueueStore
from router.core.runtime_config import RuntimeConfig


class QueueStoreTests(unittest.TestCase):
    def test_submit_claim_complete_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({"jobType": "manifest", "items": [{"id": "one"}]}), encoding="utf-8")
            store = QueueStore(root / "queue")

            submitted = store.submit_task(str(job), preset="batch_draft", providers=["p1"], max_workers=2)
            task_id = submitted["task_id"]

            self.assertEqual(submitted["schema_version"], QUEUE_TASK_SCHEMA_VERSION)
            self.assertEqual(submitted["status"], "pending")
            self.assertTrue(Path(submitted["job_path"]).exists())
            self.assertEqual(store.task_status(task_id)["preset"], "batch_draft")  # type: ignore[index]

            claimed = store.claim_next_task(worker_id="worker-1")
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["task_id"], task_id)  # type: ignore[index]
            self.assertEqual(claimed["status"], "running")  # type: ignore[index]
            self.assertIsNone(store.claim_next_task(worker_id="worker-2"))

            done = store.complete_task(
                task_id,
                summary_path="/tmp/summary.json",
                ledger_path="/tmp/ledger.json",
                success_count=1,
                failure_count=0,
            )

            self.assertEqual(done["status"], "done")
            self.assertEqual(done["summary_path"], "/tmp/summary.json")
            self.assertEqual(store.task_status(task_id)["status"], "done")  # type: ignore[index]

    def test_submit_task_records_speed_scheduling_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")

            submitted = store.submit_task(
                str(job),
                queue_priority=10,
                model_class="Snano",
                station_tier="professional_station",
                latency_budget_seconds=30,
            )

            self.assertEqual(submitted["scheduling_policy"], "speed_first_capacity_aware")
            self.assertEqual(submitted["queue_priority"], 10)
            self.assertEqual(submitted["model_class"], "Snano")
            self.assertEqual(submitted["station_tier"], "professional_station")
            self.assertEqual(submitted["latency_budget_seconds"], 30)
            self.assertIsNone(submitted["scheduler_decision"])
            self.assertIsNone(submitted["wait_reason"])
            self.assertEqual(submitted["effective_providers"], [])
            self.assertEqual(submitted["provider_waits"], [])
            self.assertFalse(submitted["secret_values_included"])

    def test_claim_task_records_scheduler_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["p1", "p2"])

            claimed = store.claim_task(
                task["task_id"],
                worker_id="worker",
                scheduler_decision={
                    "selected_reason": "fallback_provider_available",
                    "effective_providers": ["p2", "p1"],
                    "provider_waits": [{"provider": "p1", "wait_reason": "provider_capacity_full"}],
                },
            )

            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["scheduler_decision"], "selected")  # type: ignore[index]
            self.assertEqual(claimed["selected_reason"], "fallback_provider_available")  # type: ignore[index]
            self.assertEqual(claimed["effective_providers"], ["p2", "p1"])  # type: ignore[index]
            self.assertEqual(claimed["provider_waits"][0]["wait_reason"], "provider_capacity_full")  # type: ignore[index]
            self.assertIsNone(store.claim_task(task["task_id"], worker_id="other"))

    def test_update_pending_scheduling_records_wait_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["p1"])

            updated = store.update_pending_scheduling(
                task["task_id"],
                scheduler_decision="waiting",
                wait_reason="provider_capacity_full",
                provider_waits=[{"provider": "p1", "wait_reason": "provider_capacity_full"}],
            )

            self.assertEqual(updated["scheduler_decision"], "waiting")  # type: ignore[index]
            self.assertEqual(updated["wait_reason"], "provider_capacity_full")  # type: ignore[index]
            self.assertEqual(updated["provider_waits"][0]["provider"], "p1")  # type: ignore[index]

    def test_update_pending_scheduling_tolerates_concurrent_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["p1"])

            with patch.object(store, "_write_record", side_effect=FileNotFoundError):
                updated = store.update_pending_scheduling(
                    task["task_id"],
                    scheduler_decision="waiting",
                    wait_reason="provider_capacity_full",
                )

            self.assertIsNone(updated)

    def test_cancel_pending_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job))

            cancelled = store.cancel_task(task["task_id"])

            self.assertIsNotNone(cancelled)
            self.assertEqual(cancelled["status"], "cancelled")  # type: ignore[index]
            self.assertIsNone(store.claim_next_task(worker_id="worker"))

    def test_fail_running_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job))
            store.claim_next_task(worker_id="worker")

            failed = store.fail_task(task["task_id"], error="boom")

            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["error"], "boom")

    def test_wait_remote_claim_and_defer_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["apimart-gpt-image-2-images"])
            store.claim_next_task(worker_id="worker")

            waiting = store.wait_remote_task(
                task["task_id"],
                remote_async={
                    "provider": "apimart-gpt-image-2-images",
                    "task_id": "remote-1",
                    "next_poll_at": 100.0,
                    "poll_attempts": 0,
                },
            )
            self.assertEqual(waiting["status"], "waiting_remote")
            self.assertIsNone(store.claim_next_remote_task(worker_id="worker", now=99.0))

            claimed = store.claim_next_remote_task(worker_id="worker", now=100.0)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["status"], "running")  # type: ignore[index]
            self.assertEqual(claimed["remote_async"]["poll_attempts"], 1)  # type: ignore[index]

    def test_list_tasks_skips_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            bad = root / "queue" / "pending" / "bad"
            bad.mkdir(parents=True)
            (bad / "task.json").write_text("{bad", encoding="utf-8")

            self.assertEqual(store.list_tasks(), [])

    def test_runtime_config_has_queue_and_lock_dirs(self) -> None:
        config = RuntimeConfig.default()

        self.assertIn("data/queue", config.queue_dir)
        self.assertIn("data/provider-locks", config.provider_lock_dir)


if __name__ == "__main__":
    unittest.main()
