from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from router.core.provider_state_store import ProviderHealthStore
from router.core.scheduler import ProviderState


class ProviderHealthStoreTests(unittest.TestCase):
    def test_round_trips_cooldown_state_without_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "provider-health.json"
            states = {
                "p1": ProviderState(
                    cooldown_until=time.time() + 60,
                    cooldown_reason="rate_limit_or_quota",
                    last_error_category="rate_limit",
                    last_error_message="HTTP Error 429",
                    consecutive_failures=2,
                )
            }

            ProviderHealthStore(path).save(states)
            raw = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(raw["schema_version"], "provider-health/v1")
            self.assertNotIn("api_key", json.dumps(raw).lower())
            restored = ProviderHealthStore(path).load(["p1"])
            self.assertEqual(restored["p1"].cooldown_reason, "rate_limit_or_quota")
            self.assertEqual(restored["p1"].last_error_category, "rate_limit")
            self.assertEqual(restored["p1"].consecutive_failures, 2)

    def test_successful_restore_signal_clears_failure_counters(self) -> None:
        state = ProviderState(
            cooldown_until=time.time() + 60,
            cooldown_reason="rate_limit_or_quota",
            last_error_category="rate_limit",
            consecutive_failures=3,
        )

        ProviderHealthStore.apply_success(state, signal="passive_success")

        self.assertEqual(state.cooldown_until, 0.0)
        self.assertIsNone(state.cooldown_reason)
        self.assertIsNone(state.last_error_category)
        self.assertEqual(state.consecutive_failures, 0)
        self.assertEqual(state.last_recovery_signal, "passive_success")


if __name__ == "__main__":
    unittest.main()
