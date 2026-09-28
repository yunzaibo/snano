from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.batch_runner import load_job_items_with_defaults
from router.core.models import CapabilityBucket, ProviderCapability
from router.core.provider_locks import ProviderLeaseManager
from router.core.provider_state_store import ProviderHealthStore
from router.core.queue_scheduler import QueueScheduler
from router.core.queue_store import QueueStore
from router.core.scheduler import ProviderState


class QueueSchedulerTests(unittest.TestCase):
    def test_skips_locked_fifo_head_and_selects_later_runnable_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            first = self._submit(store, root, "first", ["p1"])
            second = self._submit(store, root, "second", ["p2"])
            lease = ProviderLeaseManager(root / "locks").acquire("p1", max_concurrency=1)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertIsNotNone(lease)
            self.assertEqual(decision.selected_task_id, second["task_id"])
            self.assertEqual(decision.effective_providers, ["p2"])
            waiting = {row.task_id: row for row in decision.waiting_tasks}
            self.assertEqual(waiting[first["task_id"]].wait_reason, "provider_capacity_full")

    def test_reorders_to_fallback_when_primary_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(store, root, "formal", ["p1", "p2"])
            lease = ProviderLeaseManager(root / "locks").acquire("p1", max_concurrency=1)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertIsNotNone(lease)
            self.assertEqual(decision.selected_task_id, task["task_id"])
            self.assertEqual(decision.selected_reason, "fallback_provider_available")
            self.assertEqual(decision.effective_providers, ["p2", "p1"])

    def test_fallback_still_reorders_when_primary_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(store, root, "fallback", ["p1", "p2"])
            lease = ProviderLeaseManager(root / "locks").acquire("p1", max_concurrency=1)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertIsNotNone(lease)
            self.assertEqual(decision.selected_task_id, task["task_id"])
            self.assertEqual(decision.selected_reason, "fallback_provider_available")
            self.assertEqual(decision.effective_providers, ["p2", "p1"])

    def test_running_claim_reservation_spreads_burst_to_next_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            first = self._submit(store, root, "first", ["p1", "p2"])
            second = self._submit(store, root, "second", ["p1", "p2"])
            store.claim_task(
                first["task_id"],
                worker_id="worker-1",
                scheduler_decision={
                    "selected_reason": "primary_provider_available",
                    "effective_providers": ["p1", "p2"],
                },
            )

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertEqual(decision.selected_task_id, second["task_id"])
            self.assertEqual(decision.selected_reason, "fallback_provider_available")
            self.assertEqual(decision.effective_providers, ["p2", "p1"])
            selected = decision.selected_decision()
            waits = {row.provider: row for row in selected.provider_waits} if selected else {}
            self.assertEqual(waits["p1"].wait_reason, "provider_capacity_full")
            self.assertEqual(waits["p1"].active_queue_tasks, 1)

    def test_waiting_remote_counts_against_provider_capacity_for_throughput(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            first = self._submit(store, root, "async-first", ["p1", "p2"])
            second = self._submit(store, root, "async-second", ["p1", "p2"])
            store.claim_task(
                first["task_id"],
                worker_id="worker-1",
                scheduler_decision={
                    "selected_reason": "primary_provider_available",
                    "effective_providers": ["p1", "p2"],
                },
            )
            store.wait_remote_task(
                first["task_id"],
                remote_async={
                    "provider": "p1",
                    "task_id": "remote-1",
                    "next_poll_at": time.time() + 300,
                    "poll_attempts": 0,
                },
            )

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertEqual(decision.selected_task_id, second["task_id"])
            self.assertEqual(decision.selected_reason, "fallback_provider_available")
            self.assertEqual(decision.effective_providers, ["p2", "p1"])
            selected = decision.selected_decision()
            waits = {row.provider: row for row in selected.provider_waits} if selected else {}
            self.assertEqual(waits["p1"].wait_reason, "provider_capacity_full")
            self.assertEqual(waits["p1"].active_queue_tasks, 1)

    def test_speed_first_waits_when_any_race_provider_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(store, root, "speed-race", ["p1", "p2"], routing_policy="speed_first")
            lease = ProviderLeaseManager(root / "locks").acquire("p1", max_concurrency=1)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertIsNotNone(lease)
            self.assertIsNone(decision.selected_task_id)
            self.assertEqual(decision.waiting_tasks[0].task_id, task["task_id"])
            self.assertEqual(decision.waiting_tasks[0].wait_reason, "race_group_capacity_blocked")

    def test_speed_first_ignores_locked_same_provider_group_duplicate_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(
                store,
                root,
                "same_provider_group_duplicate",
                ["p1", "p1_alt", "p2"],
                routing_policy="speed_first",
            )
            lease = ProviderLeaseManager(root / "locks").acquire("p1_alt", max_concurrency=1)

            with patch(
                "router.core.queue_scheduler.provider_group_for",
                side_effect=lambda provider: {"p1": "g1", "p1_alt": "g1", "p2": "g2"}[provider],
            ):
                decision = QueueScheduler(
                    queue_dir=root / "queue",
                    provider_lock_dir=root / "locks",
                ).evaluate()

            self.assertIsNotNone(lease)
            self.assertEqual(decision.selected_task_id, task["task_id"])
            self.assertEqual(decision.selected_reason, "primary_provider_available")
            self.assertEqual(decision.effective_providers, ["p1", "p1_alt", "p2"])
            selected = decision.selected_decision()
            waits = {row.provider: row.wait_reason for row in selected.provider_waits} if selected else {}
            self.assertEqual(waits["p1_alt"], "race_group_capacity_blocked")

    def test_speed_first_blocks_when_selected_same_provider_group_lane_is_locked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(
                store,
                root,
                "same_provider_group_selected",
                ["p1", "p1_alt", "p2"],
                routing_policy="speed_first",
            )
            lease = ProviderLeaseManager(root / "locks").acquire("p1", max_concurrency=1)

            with patch(
                "router.core.queue_scheduler.provider_group_for",
                side_effect=lambda provider: {"p1": "g1", "p1_alt": "g1", "p2": "g2"}[provider],
            ):
                decision = QueueScheduler(
                    queue_dir=root / "queue",
                    provider_lock_dir=root / "locks",
                ).evaluate()

            self.assertIsNotNone(lease)
            self.assertIsNone(decision.selected_task_id)
            self.assertEqual(decision.waiting_tasks[0].task_id, task["task_id"])
            self.assertEqual(decision.waiting_tasks[0].wait_reason, "race_group_capacity_blocked")

    def test_speed_priority_beats_normal_priority(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            normal = self._submit(store, root, "normal", ["p1"], queue_priority=60)
            speed = self._submit(store, root, "speed", ["p2"], queue_priority=10)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            self.assertEqual(normal["status"], "pending")
            self.assertEqual(decision.selected_task_id, speed["task_id"])
            self.assertEqual(decision.selected_reason, "speed_priority")

    def test_aging_can_lift_old_waiting_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            old = self._submit(store, root, "old", ["p1"], queue_priority=60)
            new = self._submit(store, root, "new", ["p2"], queue_priority=10)
            self._rewrite_created_at(root / "queue", old["task_id"], time.time() - 600)

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
                aging_seconds=300,
            ).evaluate()

            self.assertEqual(decision.selected_task_id, old["task_id"])
            self.assertEqual(decision.selected_reason, "aged_priority")
            self.assertNotEqual(decision.selected_task_id, new["task_id"])

    def test_cooldown_task_waits_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit(store, root, "cooldown", ["p1"])
            state_path = root / "provider-health.json"
            ProviderHealthStore(state_path).save({
                "p1": ProviderState(cooldown_until=time.time() + 300, cooldown_reason="rate_limit")
            })

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
                provider_state_path=state_path,
            ).evaluate()

            self.assertIsNone(decision.selected_task_id)
            self.assertEqual(decision.waiting_tasks[0].task_id, task["task_id"])
            self.assertEqual(decision.waiting_tasks[0].wait_reason, "provider_cooldown")

    def test_no_secret_values_in_scheduler_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            self._submit(store, root, "secret", ["p1"])

            decision = QueueScheduler(
                queue_dir=root / "queue",
                provider_lock_dir=root / "locks",
            ).evaluate()

            payload = json.dumps(decision.to_dict(), ensure_ascii=False)
            self.assertFalse(decision.secret_values_included)
            self.assertNotIn("sk-", payload)
            self.assertNotIn("Authorization", payload)
            self.assertNotIn("Cookie", payload)

    def test_multi_reference_i1_lane_allows_reference_downgrade_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit_i2i(
                store,
                root,
                "downgrade",
                ["i1"],
                references=["ref-a.png", "ref-b.png"],
            )

            with patch(
                "router.core.queue_scheduler.provider_capability_registry",
                return_value={"i1": self._capability("i1", CapabilityBucket.SINGLE_REFERENCE, max_refs=1)},
            ):
                decision = QueueScheduler(
                    queue_dir=root / "queue",
                    provider_lock_dir=root / "locks",
                ).evaluate()

            self.assertEqual(decision.selected_task_id, task["task_id"])
            self.assertEqual(check_reason := decision.selected_reason, "primary_provider_available")
            self.assertEqual(check_reason, "primary_provider_available")
            self.assertEqual(decision.effective_providers, ["i1"])
            self.assertEqual(
                decision.selected_decision().provider_waits if decision.selected_decision() else [],
                [],
                "reference_downgrade_allowed should be runnable, not a wait reason",
            )

    def test_full_reference_lock_rejects_i1_and_selects_im_lane(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            task = self._submit_i2i(
                store,
                root,
                "full-lock",
                ["i1", "im"],
                references=["ref-a.png", "ref-b.png"],
                metadata={"requireFullReferenceLock": True},
            )

            with patch(
                "router.core.queue_scheduler.provider_capability_registry",
                return_value={
                    "i1": self._capability("i1", CapabilityBucket.SINGLE_REFERENCE, max_refs=1),
                    "im": self._capability("im", CapabilityBucket.MULTI_REFERENCE, max_refs=5),
                },
            ):
                decision = QueueScheduler(
                    queue_dir=root / "queue",
                    provider_lock_dir=root / "locks",
                ).evaluate()

            self.assertEqual(decision.selected_task_id, task["task_id"])
            self.assertEqual(decision.selected_reason, "fallback_provider_available")
            self.assertEqual(decision.effective_providers, ["im", "i1"])
            selected = decision.selected_decision()
            waits = {row.provider: row.wait_reason for row in selected.provider_waits} if selected else {}
            self.assertEqual(waits["i1"], "unsupported_request")

    def test_evaluate_parses_each_pending_job_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = QueueStore(root / "queue")
            self._submit(store, root, "first", ["p1"])
            self._submit(store, root, "second", ["p2"])

            with patch(
                "router.core.queue_scheduler.load_job_items_with_defaults",
                wraps=load_job_items_with_defaults,
            ) as load_items:
                QueueScheduler(
                    queue_dir=root / "queue",
                    provider_lock_dir=root / "locks",
                ).evaluate()

            self.assertEqual(load_items.call_count, 2)

    def _submit(
        self,
        store: QueueStore,
        root: Path,
        name: str,
        providers: list[str],
        *,
        queue_priority: int | None = None,
        routing_policy: str | None = None,
    ) -> dict:
        job = root / f"{name}.json"
        job.write_text(json.dumps({
            "jobType": "manifest",
            "items": [
                {"id": name, "requestType": "text_to_image", "prompt": name},
            ],
        }), encoding="utf-8")
        return store.submit_task(
            str(job),
            providers=providers,
            queue_priority=queue_priority,
            routing_policy=routing_policy,
        )

    def _submit_i2i(
        self,
        store: QueueStore,
        root: Path,
        name: str,
        providers: list[str],
        *,
        references: list[str],
        metadata: dict | None = None,
    ) -> dict:
        job = root / f"{name}.json"
        job.write_text(json.dumps({
            "jobType": "manifest",
            "items": [
                {
                    "id": name,
                    "requestType": "image_to_image",
                    "prompt": name,
                    "referenceImages": references,
                    "metadata": metadata or {},
                },
            ],
        }), encoding="utf-8")
        return store.submit_task(str(job), providers=providers)

    def _capability(
        self,
        provider: str,
        bucket: CapabilityBucket,
        *,
        max_refs: int,
    ) -> ProviderCapability:
        return ProviderCapability(
            provider=provider,
            bucket=bucket,
            supports_text_to_image=True,
            supports_image_to_image=bucket in {CapabilityBucket.SINGLE_REFERENCE, CapabilityBucket.MULTI_REFERENCE},
            supports_multi_reference=bucket == CapabilityBucket.MULTI_REFERENCE,
            max_reference_images=max_refs,
            max_concurrency=1,
            enabled=True,
        )

    def _rewrite_created_at(self, queue_dir: Path, task_id: str, created_at: float) -> None:
        task_path = queue_dir / "pending" / task_id / "task.json"
        data = json.loads(task_path.read_text(encoding="utf-8"))
        data["created_at"] = created_at
        task_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
