from __future__ import annotations

import unittest

from router.core.routing_policy import resolve_routing_policy


class RoutingPolicyTests(unittest.TestCase):
    def test_speed_first_uses_race_without_same_provider_retries(self) -> None:
        plan = resolve_routing_policy(
            "speed_first",
            ["vectorengine", "vectorengine_compat", "gptge"],
        )

        self.assertEqual(plan.policy, "speed_first")
        self.assertEqual(plan.routing_mode, "race")
        self.assertEqual(plan.max_retries_per_provider, 0)
        self.assertEqual(plan.race_providers, ["vectorengine", "vectorengine_compat", "gptge"])

    def test_conservative_retry_increases_same_provider_retry_budget(self) -> None:
        plan = resolve_routing_policy("conservative_retry", ["p1", "p2"])

        self.assertEqual(plan.routing_mode, "fallback")
        self.assertGreaterEqual(plan.max_retries_per_provider, 2)

    def test_load_balance_uses_fallback_without_same_provider_retries(self) -> None:
        plan = resolve_routing_policy("load_balance", ["p1", "p2"])

        self.assertEqual(plan.policy, "load_balance")
        self.assertEqual(plan.routing_mode, "fallback")
        self.assertEqual(plan.max_retries_per_provider, 0)
        self.assertEqual(plan.provider_order, ["p1", "p2"])
        self.assertTrue(plan.allow_fallback)

    def test_weighted_load_balance_uses_fallback_without_same_provider_retries(self) -> None:
        plan = resolve_routing_policy("weighted_load_balance", ["p1", "p2"])

        self.assertEqual(plan.policy, "weighted_load_balance")
        self.assertEqual(plan.routing_mode, "fallback")
        self.assertEqual(plan.max_retries_per_provider, 0)
        self.assertEqual(plan.provider_order, ["p1", "p2"])
        self.assertTrue(plan.allow_fallback)

    def test_health_aware_load_balance_uses_fallback_without_same_provider_retries(self) -> None:
        plan = resolve_routing_policy("health_aware_load_balance", ["p1", "p2"])

        self.assertEqual(plan.policy, "health_aware_load_balance")
        self.assertEqual(plan.routing_mode, "fallback")
        self.assertEqual(plan.max_retries_per_provider, 0)
        self.assertEqual(plan.provider_order, ["p1", "p2"])
        self.assertTrue(plan.allow_fallback)

    def test_fallback_load_balance_and_race_have_different_execution_shapes(self) -> None:
        fallback = resolve_routing_policy("fallback", ["p1", "p2", "p3"])
        load_balance = resolve_routing_policy("load_balance", ["p2", "p3", "p1"])
        health_aware = resolve_routing_policy("health_aware_load_balance", ["p3", "p1", "p2"])
        race = resolve_routing_policy("speed_first", ["p1", "p2", "p3"])

        self.assertEqual(fallback.routing_mode, "fallback")
        self.assertIsNone(fallback.max_retries_per_provider)
        self.assertEqual(fallback.race_providers, [])
        self.assertEqual(fallback.provider_order, ["p1", "p2", "p3"])

        self.assertEqual(load_balance.routing_mode, "fallback")
        self.assertEqual(load_balance.max_retries_per_provider, 0)
        self.assertEqual(load_balance.race_providers, [])
        self.assertEqual(load_balance.provider_order, ["p2", "p3", "p1"])

        self.assertEqual(health_aware.routing_mode, "fallback")
        self.assertEqual(health_aware.max_retries_per_provider, 0)
        self.assertEqual(health_aware.race_providers, [])
        self.assertEqual(health_aware.provider_order, ["p3", "p1", "p2"])

        self.assertEqual(race.routing_mode, "race")
        self.assertEqual(race.max_retries_per_provider, 0)
        self.assertEqual(race.race_providers, ["p1", "p2", "p3"])

    def test_unknown_policy_falls_back_to_runtime_defaults_with_warning(self) -> None:
        plan = resolve_routing_policy("unknown_policy", ["p1"])

        self.assertEqual(plan.policy, "fallback")
        self.assertIn("unknown_policy", plan.warnings[0])


if __name__ == "__main__":
    unittest.main()
