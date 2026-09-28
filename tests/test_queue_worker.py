from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from router.core.batch_runner import BatchItemResult, BatchRunResult
from router.core.queue_store import QueueStore
from router.core.queue_worker import QueueWorker, QueueWorkerConfig


class QueueWorkerTests(unittest.TestCase):
    def test_worker_claims_and_completes_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({"jobType": "manifest", "items": [{"id": "one"}]}), encoding="utf-8")
            queue_dir = root / "queue"
            task = QueueStore(queue_dir).submit_task(str(job), providers=["p1"])

            fake_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )
            with patch("router.core.queue_worker.run_job_file", return_value=fake_result) as run_job:
                row = QueueWorker(self._config(root)).run_once()

            self.assertEqual(row["status"], "done")  # type: ignore[index]
            self.assertEqual(row["task_id"], task["task_id"])  # type: ignore[index]
            self.assertEqual(row["summary_path"], str(root / "summary.json"))  # type: ignore[index]
            self.assertTrue(run_job.call_args.kwargs["cross_process_provider_locks"])
            self.assertEqual(run_job.call_args.kwargs["task_id"], task["task_id"])

    def test_worker_skips_locked_fifo_head_and_runs_later_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            first_job = root / "first.json"
            second_job = root / "second.json"
            payload = {"jobType": "manifest", "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}]}
            first_job.write_text(json.dumps(payload), encoding="utf-8")
            second_job.write_text(json.dumps(payload), encoding="utf-8")
            first = store.submit_task(str(first_job), providers=["p1"])
            second = store.submit_task(str(second_job), providers=["p2"])
            from router.core.provider_locks import ProviderLeaseManager
            lease = ProviderLeaseManager(root / "provider-locks").acquire("p1", max_concurrency=1)
            fake_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )

            with patch("router.core.queue_worker.run_job_file", return_value=fake_result) as run_job:
                row = QueueWorker(self._config(root)).run_once()

            self.assertIsNotNone(lease)
            self.assertEqual(row["task_id"], second["task_id"])  # type: ignore[index]
            self.assertEqual(store.task_status(first["task_id"])["status"], "pending")  # type: ignore[index]
            self.assertEqual(store.task_status(first["task_id"])["wait_reason"], "provider_capacity_full")  # type: ignore[index]
            self.assertEqual(run_job.call_args.kwargs["providers"], ["p2"])

    def test_worker_passes_fallback_effective_provider_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}],
            }), encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["p1", "p2"])
            from router.core.provider_locks import ProviderLeaseManager
            lease = ProviderLeaseManager(root / "provider-locks").acquire("p1", max_concurrency=1)
            fake_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )

            with patch("router.core.queue_worker.run_job_file", return_value=fake_result) as run_job:
                row = QueueWorker(self._config(root)).run_once()

            self.assertIsNotNone(lease)
            self.assertEqual(row["task_id"], task["task_id"])  # type: ignore[index]
            self.assertEqual(row["selected_reason"], "fallback_provider_available")  # type: ignore[index]
            self.assertEqual(run_job.call_args.kwargs["providers"], ["p2", "p1"])

    def test_worker_leaves_speed_first_blocked_and_runs_later_runnable_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"jobType": "manifest", "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}]}
            speed_job = root / "speed.json"
            normal_job = root / "normal.json"
            speed_job.write_text(json.dumps(payload), encoding="utf-8")
            normal_job.write_text(json.dumps(payload), encoding="utf-8")
            store = QueueStore(root / "queue")
            speed = store.submit_task(str(speed_job), providers=["p1", "p2"], routing_policy="speed_first")
            normal = store.submit_task(str(normal_job), providers=["p3"])
            from router.core.provider_locks import ProviderLeaseManager
            lease = ProviderLeaseManager(root / "provider-locks").acquire("p1", max_concurrency=1)
            fake_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )

            with patch("router.core.queue_worker.run_job_file", return_value=fake_result) as run_job:
                row = QueueWorker(self._config(root)).run_once()

            self.assertIsNotNone(lease)
            self.assertEqual(row["task_id"], normal["task_id"])  # type: ignore[index]
            self.assertEqual(run_job.call_args.kwargs["providers"], ["p3"])
            self.assertEqual(store.task_status(speed["task_id"])["status"], "pending")  # type: ignore[index]
            self.assertEqual(store.task_status(speed["task_id"])["wait_reason"], "race_group_capacity_blocked")  # type: ignore[index]

    def test_worker_returns_none_when_no_task_is_runnable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}],
            }), encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["p1"])
            from router.core.provider_locks import ProviderLeaseManager
            lease = ProviderLeaseManager(root / "provider-locks").acquire("p1", max_concurrency=1)

            row = QueueWorker(self._config(root)).run_once()

            self.assertIsNotNone(lease)
            self.assertIsNone(row)
            self.assertEqual(store.task_status(task["task_id"])["status"], "pending")  # type: ignore[index]
            self.assertEqual(store.task_status(task["task_id"])["wait_reason"], "provider_capacity_full")  # type: ignore[index]

    def test_worker_retries_stale_scheduler_selection_beyond_two_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}],
            }), encoding="utf-8")
            store = QueueStore(root / "queue")
            submitted = store.submit_task(str(job), providers=["p1"])
            worker = QueueWorker(self._config(root))
            original_claim = worker.store.claim_task
            attempts = {"count": 0}

            def stale_then_claim(*args, **kwargs):
                attempts["count"] += 1
                if attempts["count"] <= 2:
                    return None
                return original_claim(*args, **kwargs)

            with patch.object(worker.store, "claim_task", side_effect=stale_then_claim):
                task = worker._claim_scheduled_task()

            self.assertIsNotNone(task)
            self.assertEqual(task["task_id"], submitted["task_id"])  # type: ignore[index]
            self.assertEqual(attempts["count"], 3)

    def test_worker_marks_generation_failures_as_failed_with_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            QueueStore(root / "queue").submit_task(str(job))
            fake_result = SimpleNamespace(
                failure_count=2,
                success_count=0,
                summary_path=str(root / "summary.json"),
                ledger_path=str(root / "ledger.json"),
            )

            with patch("router.core.queue_worker.run_job_file", return_value=fake_result):
                row = QueueWorker(self._config(root)).run_once()

            self.assertEqual(row["status"], "failed")  # type: ignore[index]
            self.assertEqual(row["failure_count"], 2)  # type: ignore[index]
            self.assertEqual(row["summary_path"], str(root / "summary.json"))  # type: ignore[index]

    def test_worker_defers_async_remote_submit_and_frees_worker_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}],
            }), encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job), providers=["apimart-gpt-image-2-images"])
            async_result = BatchRunResult(
                job_type="manifest",
                item_results=[
                    BatchItemResult(
                        item_id="one",
                        request_hash="abc",
                        request_type="text_to_image",
                        status="failed",
                        selected_provider="apimart-gpt-image-2-images",
                        attempted_providers=["apimart-gpt-image-2-images"],
                        attempts=[{
                            "provider": "apimart-gpt-image-2-images",
                            "error_code": "async_pending",
                            "remote_task_id": "remote-1",
                        }],
                        error_code="async_pending",
                    )
                ],
                summary_path=str(root / "submit-summary.json"),
                ledger_path=str(root / "submit-ledger.json"),
                success_count=0,
                failure_count=1,
            )

            with (
                patch("router.core.queue_worker.load_lane_configs", return_value={
                    "apimart-gpt-image-2-images": SimpleNamespace(async_remote=True),
                }),
                patch("router.core.queue_worker.run_job_file", return_value=async_result) as run_job,
            ):
                row = QueueWorker(self._config(root)).run_once()

            self.assertEqual(row["status"], "waiting_remote")  # type: ignore[index]
            self.assertEqual(row["task_id"], task["task_id"])  # type: ignore[index]
            self.assertEqual(row["remote_async"]["task_id"], "remote-1")  # type: ignore[index]
            self.assertTrue(run_job.call_args.kwargs["async_submit_only"])

    def test_worker_can_run_later_task_after_async_remote_is_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = {"jobType": "manifest", "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}]}
            async_job = root / "async.json"
            fast_job = root / "fast.json"
            async_job.write_text(json.dumps(payload), encoding="utf-8")
            fast_job.write_text(json.dumps(payload), encoding="utf-8")
            store = QueueStore(root / "queue")
            async_task = store.submit_task(str(async_job), providers=["apimart-gpt-image-2-images"])
            fast_task = store.submit_task(str(fast_job), providers=["rightcodes-gpt-image-2-images"])
            async_result = BatchRunResult(
                job_type="manifest",
                item_results=[
                    BatchItemResult(
                        item_id="one",
                        request_hash="abc",
                        request_type="text_to_image",
                        status="failed",
                        selected_provider="apimart-gpt-image-2-images",
                        attempted_providers=["apimart-gpt-image-2-images"],
                        attempts=[{"provider": "apimart-gpt-image-2-images", "remote_task_id": "remote-1"}],
                        error_code="async_pending",
                    )
                ],
                summary_path=str(root / "submit-summary.json"),
                ledger_path=str(root / "submit-ledger.json"),
                success_count=0,
                failure_count=1,
            )
            fast_result = SimpleNamespace(
                failure_count=0,
                success_count=1,
                summary_path=str(root / "fast-summary.json"),
                ledger_path=str(root / "fast-ledger.json"),
            )

            with (
                patch("router.core.queue_worker.load_lane_configs", return_value={
                    "apimart-gpt-image-2-images": SimpleNamespace(async_remote=True),
                    "rightcodes-gpt-image-2-images": SimpleNamespace(async_remote=False),
                }),
                patch("router.core.queue_worker.run_job_file", side_effect=[async_result, fast_result]),
            ):
                first = QueueWorker(self._config(root)).run_once()
                second = QueueWorker(self._config(root)).run_once()

            self.assertEqual(first["task_id"], async_task["task_id"])  # type: ignore[index]
            self.assertEqual(first["status"], "waiting_remote")  # type: ignore[index]
            self.assertEqual(second["task_id"], fast_task["task_id"])  # type: ignore[index]
            self.assertEqual(second["status"], "done")  # type: ignore[index]

    def test_worker_skips_cancelled_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text("{}", encoding="utf-8")
            store = QueueStore(root / "queue")
            task = store.submit_task(str(job))
            store.cancel_task(task["task_id"])

            row = QueueWorker(self._config(root)).run_once()

            self.assertIsNone(row)

    def _config(self, root: Path) -> QueueWorkerConfig:
        return QueueWorkerConfig(
            queue_dir=str(root / "queue"),
            output_dir=str(root / "artifacts"),
            batch_dir=str(root / "batches"),
            ledger_dir=str(root / "ledger"),
            perf_log_path=str(root / "perf.jsonl"),
            provider_state_path=str(root / "provider-health.json"),
            provider_lock_dir=str(root / "provider-locks"),
            worker_id="test-worker",
        )


if __name__ == "__main__":
    unittest.main()
