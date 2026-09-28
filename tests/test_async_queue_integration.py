from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.queue_store import QueueStore
from router.core.queue_worker import QueueWorker, QueueWorkerConfig
from tests.test_batch_runner_health import FakeAdapter, success_result


class AsyncQueueIntegrationTests(unittest.TestCase):
    def test_two_tasks_share_provider_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            lock_dir = root / "provider-locks"
            store = QueueStore(queue_dir)
            for index in range(2):
                job = root / f"job-{index}.json"
                job.write_text(json.dumps({
                    "jobType": "manifest",
                    "items": [
                        {"id": f"item-{index}", "requestType": "text_to_image", "prompt": f"item {index}"},
                    ],
                }), encoding="utf-8")
                store.submit_task(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_file=str(root / "perf.jsonl"),
                    provider_state_file=str(root / "provider-health.json"),
                )

            adapters = {
                "p1": FakeAdapter("p1", [success_result("p1"), success_result("p1")]),
            }
            worker = QueueWorker(QueueWorkerConfig(
                queue_dir=str(queue_dir),
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                perf_log_path=str(root / "perf.jsonl"),
                provider_state_path=str(root / "provider-health.json"),
                provider_lock_dir=str(lock_dir),
                worker_id="integration-worker",
            ))

            with patch("router.core.batch_runner._build_adapters", return_value=(adapters, {})):
                first = worker.run_once()
                second = worker.run_once()

            self.assertEqual(first["status"], "done")  # type: ignore[index]
            self.assertEqual(second["status"], "done")  # type: ignore[index]
            self.assertTrue(Path(first["summary_path"]).exists())  # type: ignore[index]
            self.assertTrue(Path(second["summary_path"]).exists())  # type: ignore[index]
            self.assertEqual(list((lock_dir / "p1").glob("lease-*.json")), [])

    def test_two_workers_do_not_claim_same_scheduler_selected_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_dir = root / "queue"
            lock_dir = root / "provider-locks"
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "item", "requestType": "text_to_image", "prompt": "item"},
                ],
            }), encoding="utf-8")
            QueueStore(queue_dir).submit_task(
                str(job),
                providers=["p1"],
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                perf_log_file=str(root / "perf.jsonl"),
                provider_state_file=str(root / "provider-health.json"),
            )
            adapters = {"p1": FakeAdapter("p1", [success_result("p1")])}
            config = QueueWorkerConfig(
                queue_dir=str(queue_dir),
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                perf_log_path=str(root / "perf.jsonl"),
                provider_state_path=str(root / "provider-health.json"),
                provider_lock_dir=str(lock_dir),
                worker_id="worker-a",
            )

            with patch("router.core.batch_runner._build_adapters", return_value=(adapters, {})):
                first = QueueWorker(config).run_once()
                second = QueueWorker(QueueWorkerConfig(
                    queue_dir=str(queue_dir),
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(root / "perf.jsonl"),
                    provider_state_path=str(root / "provider-health.json"),
                    provider_lock_dir=str(lock_dir),
                    worker_id="worker-b",
                )).run_once()

            self.assertEqual(first["status"], "done")  # type: ignore[index]
            self.assertIsNone(second)
            self.assertEqual(len(QueueStore(queue_dir).list_tasks(statuses=["done"])), 1)


if __name__ == "__main__":
    unittest.main()
