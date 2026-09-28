from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from router.core.provider_locks import ProviderLeaseManager


class ProviderLeaseManagerTests(unittest.TestCase):
    def test_acquire_respects_max_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProviderLeaseManager(tmp, lease_timeout_seconds=60)

            first = manager.acquire("p1", max_concurrency=1, task_id="t1")
            second = manager.acquire("p1", max_concurrency=1, task_id="t2")

            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertEqual(len(manager.active_leases("p1")), 1)

            manager.release(first)
            third = manager.acquire("p1", max_concurrency=1, task_id="t3")
            self.assertIsNotNone(third)

    def test_acquire_allows_multiple_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProviderLeaseManager(tmp, lease_timeout_seconds=60)

            first = manager.acquire("p1", max_concurrency=2)
            second = manager.acquire("p1", max_concurrency=2)
            third = manager.acquire("p1", max_concurrency=2)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertIsNone(third)
            self.assertEqual(len(manager.active_leases("p1")), 2)

    def test_stale_lease_is_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProviderLeaseManager(tmp, lease_timeout_seconds=-1)

            first = manager.acquire("p1", max_concurrency=1)
            second = manager.acquire("p1", max_concurrency=1)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)

    def test_provider_id_is_path_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = ProviderLeaseManager(tmp, lease_timeout_seconds=60)

            lease = manager.acquire("../bad/provider", max_concurrency=1)

            self.assertIsNotNone(lease)
            self.assertTrue(str(lease.path).startswith(str(Path(tmp))))
            self.assertNotIn("..", lease.path.relative_to(tmp).as_posix())


if __name__ == "__main__":
    unittest.main()
