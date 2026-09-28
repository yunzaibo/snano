from __future__ import annotations

import time
import unittest

from router.core.probe_policy import probe_decision
from router.core.scheduler import ProviderState


class ProbePolicyTests(unittest.TestCase):
    def test_active_cooldown_blocks_unsolicited_probe(self) -> None:
        state = ProviderState(cooldown_until=time.time() + 60)

        decision = probe_decision("p1", state, requested=False)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "cooldown_active")
        self.assertEqual(decision.probe_type, "none")

    def test_on_demand_probe_after_cooldown_uses_safe_health_check(self) -> None:
        state = ProviderState(cooldown_until=time.time() - 1)

        decision = probe_decision("p1", state, requested=True)

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.probe_type, "health_check")
        self.assertFalse(decision.real_image_generation)

    def test_low_frequency_probe_requires_interval(self) -> None:
        state = ProviderState(last_probe_at=time.time())

        decision = probe_decision("p1", state, requested=False, min_interval_seconds=3600)

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "probe_interval_not_elapsed")


if __name__ == "__main__":
    unittest.main()
