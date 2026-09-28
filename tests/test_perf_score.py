from __future__ import annotations

import unittest

from router.core.perf_score import (
    pool_correlation_signals,
    pool_scores,
    pool_status_counts,
    provider_scores,
    provider_weight_recommendations,
    weighted_provider_slots,
)


class PerfScoreTests(unittest.TestCase):
    def test_scores_prefer_successful_lower_latency_provider(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "slow", "ok": True, "latency_ms": 40000},
            {"event": "generation_result", "provider": "fast", "ok": True, "latency_ms": 20000},
            {"event": "generation_result", "provider": "broken", "ok": False, "error_message": "HTTP Error 401: Unauthorized"},
        ])

        self.assertEqual([row["provider"] for row in scores], ["fast", "slow", "broken"])
        self.assertEqual(scores[0]["p50_latency_ms"], 20000)
        self.assertEqual(scores[-1]["errors"], {"auth": 1})

    def test_scores_treat_success_with_invalid_artifact_as_failed_attempt(self) -> None:
        scores = provider_scores([
            {
                "event": "generation_result",
                "provider": "bad-fast",
                "ok": True,
                "latency_ms": 1000,
                "artifact_metrics": {
                    "artifact_count": 1,
                    "valid_image_count": 0,
                    "invalid_image_count": 1,
                },
            },
            {
                "event": "generation_result",
                "provider": "good-slow",
                "ok": True,
                "latency_ms": 30000,
                "artifact_metrics": {
                    "artifact_count": 1,
                    "valid_image_count": 1,
                    "invalid_image_count": 0,
                    "aspect_ratio_mismatch_count": 1,
                },
            },
        ])

        rows = {row["provider"]: row for row in scores}
        self.assertEqual([row["provider"] for row in scores], ["good-slow", "bad-fast"])
        self.assertEqual(rows["bad-fast"]["success_count"], 0)
        self.assertEqual(rows["bad-fast"]["errors"], {"invalid_artifact": 1})
        self.assertEqual(rows["bad-fast"]["invalid_image_count"], 1)
        self.assertEqual(rows["good-slow"]["success_rate"], 1.0)
        self.assertEqual(rows["good-slow"]["aspect_ratio_mismatch_count"], 1)

    def test_scores_can_ignore_events_outside_recent_window(self) -> None:
        scores = provider_scores(
            [
                {
                    "event": "generation_result",
                    "provider": "old-winner",
                    "ok": True,
                    "latency_ms": 1000,
                    "recorded_at": 0,
                },
                {
                    "event": "generation_result",
                    "provider": "recent-winner",
                    "ok": True,
                    "latency_ms": 20000,
                    "recorded_at": 9990,
                },
            ],
            window_seconds=3600,
            now=10000,
        )

        self.assertEqual([row["provider"] for row in scores], ["recent-winner"])
        self.assertEqual(scores[0]["window_seconds"], 3600)

    def test_scores_ignore_subsecond_success_as_non_real_latency_sample(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "fake-fast", "ok": True, "latency_ms": 10},
            {"event": "generation_result", "provider": "real-slower", "ok": True, "latency_ms": 12000},
        ])

        rows = {row["provider"]: row for row in scores}
        self.assertEqual(rows["fake-fast"]["success_count"], 0)
        self.assertEqual(rows["fake-fast"]["ignored_fast_success_count"], 1)
        self.assertIsNone(rows["fake-fast"]["p50_latency_ms"])
        self.assertEqual(rows["real-slower"]["success_count"], 1)

    def test_weight_recommendations_explain_weighted_slots(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "fast", "ok": True, "latency_ms": 12000},
            {"event": "generation_result", "provider": "slow", "ok": True, "latency_ms": 70000},
            {"event": "generation_result", "provider": "broken", "ok": False, "error_message": "HTTP Error 401: Unauthorized"},
        ])

        rows = provider_weight_recommendations(scores, provider_order=["fast", "slow", "unknown", "broken"])

        self.assertEqual([row["provider"] for row in rows], ["fast", "slow", "unknown", "broken"])
        self.assertEqual(rows[0]["recommended_weight"], 3)
        self.assertEqual(rows[0]["reason"], "high_success_low_latency")
        self.assertEqual(rows[2]["reason"], "unknown_or_stale_kept_for_recovery")
        self.assertEqual(rows[3]["reason"], "recent_failures_retained_at_low_weight")
        self.assertEqual(
            weighted_provider_slots(scores, provider_order=["fast", "slow", "unknown", "broken"]),
            ["fast", "fast", "fast", "slow", "slow", "unknown", "broken"],
        )

    def test_weight_recommendations_respect_latency_budget(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "over-budget", "ok": True, "latency_ms": 45000},
            {"event": "generation_result", "provider": "within-budget", "ok": True, "latency_ms": 25000},
        ])

        rows = provider_weight_recommendations(
            scores,
            provider_order=["over-budget", "within-budget"],
            latency_budget_ms=30_000,
        )
        by_provider = {row["provider"]: row for row in rows}

        self.assertEqual(by_provider["within-budget"]["recommended_weight"], 3)
        self.assertEqual(by_provider["over-budget"]["recommended_weight"], 2)
        self.assertEqual(by_provider["over-budget"]["reason"], "high_success_over_latency_budget")

    def test_weight_recommendations_do_not_expand_explicit_provider_order_from_history(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "allowed", "ok": True, "latency_ms": 12000},
            {"event": "generation_result", "provider": "old-unconfigured", "ok": True, "latency_ms": 12000},
        ])

        rows = provider_weight_recommendations(scores, provider_order=["allowed"])

        self.assertEqual([row["provider"] for row in rows], ["allowed"])
        self.assertEqual(weighted_provider_slots(scores, provider_order=["allowed"]), ["allowed", "allowed", "allowed"])

    def test_weight_recommendations_keep_invalid_artifact_lane_at_low_weight(self) -> None:
        scores = provider_scores([
            {
                "event": "generation_result",
                "provider": "bad-fast",
                "ok": True,
                "latency_ms": 1000,
                "artifact_metrics": {
                    "artifact_count": 1,
                    "valid_image_count": 0,
                    "invalid_image_count": 1,
                },
            },
            {
                "event": "generation_result",
                "provider": "good-slow",
                "ok": True,
                "latency_ms": 45000,
                "artifact_metrics": {
                    "artifact_count": 1,
                    "valid_image_count": 1,
                    "invalid_image_count": 0,
                    "aspect_ratio_mismatch_count": 1,
                },
            },
        ])

        rows = provider_weight_recommendations(scores, provider_order=["bad-fast", "good-slow"])
        by_provider = {row["provider"]: row for row in rows}

        self.assertEqual(by_provider["bad-fast"]["recommended_weight"], 1)
        self.assertEqual(by_provider["bad-fast"]["reason"], "recent_failures_retained_at_low_weight")
        self.assertEqual(by_provider["bad-fast"]["invalid_image_count"], 1)
        self.assertEqual(by_provider["good-slow"]["recommended_weight"], 2)
        self.assertEqual(by_provider["good-slow"]["aspect_ratio_mismatch_count"], 1)

    def test_weight_recommendations_explain_pool_level_risk(self) -> None:
        scores = provider_scores([
            {"event": "generation_result", "provider": "new-lane", "ok": False, "error_message": "HTTP Error 429: Too Many Requests"},
        ])
        pools = pool_scores([
            {
                "event": "batch_summary",
                "route_decisions": {
                    "one": {
                        "attempts": [
                            {
                                "provider": "old-lane",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": False,
                                "error_message": "HTTP Error 429: Too Many Requests",
                            },
                            {
                                "provider": "new-lane",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": False,
                                "error_message": "HTTP Error 429: Too Many Requests",
                            },
                        ],
                    }
                },
            }
        ])

        rows = provider_weight_recommendations(
            scores,
            provider_order=["new-lane", "other-pool-lane"],
            provider_pool_map={
                "new-lane": "gptge-group/gptge-main/gptge-primary",
                "other-pool-lane": "other-group/other/other",
            },
            pools=pools,
        )

        by_provider = {row["provider"]: row for row in rows}
        self.assertEqual(by_provider["new-lane"]["reason"], "pool_recent_failures_retained_at_low_weight")
        self.assertEqual(by_provider["new-lane"]["pool_attempt_count"], 2)
        self.assertEqual(by_provider["new-lane"]["pool_success_rate"], 0.0)
        self.assertEqual(by_provider["other-pool-lane"]["reason"], "unknown_or_stale_kept_for_recovery")

    def test_pool_correlation_signals_flag_same_item_group_failures(self) -> None:
        signals = pool_correlation_signals([
            {
                "event": "batch_summary",
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "gptge-key1",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": False,
                                "error_category": "rate_limit",
                            },
                            {
                                "provider": "gptge-key2",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": False,
                                "error_category": "rate_limit",
                            },
                            {
                                "provider": "aiwave-key1",
                                "provider_group": "aiwave-group",
                                "pool_identity": "aiwave-group/aiwave-main/aiwave-primary",
                                "ok": False,
                                "error_category": "timeout",
                            },
                        ],
                    }
                },
            }
        ])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["provider_group"], "gptge-group")
        self.assertEqual(signals[0]["pool_identity"], "gptge-group/gptge-main/gptge-primary")
        self.assertEqual(signals[0]["failure_category"], "rate_limit")
        self.assertEqual(signals[0]["providers"], ["gptge-key1", "gptge-key2"])
        self.assertEqual(
            signals[0]["interpretation"],
            "check_shared_quota_account_group_ip_risk_or_upstream_queue_before_treating_as_independent",
        )

    def test_pool_correlation_signals_treat_invalid_artifacts_as_failures(self) -> None:
        signals = pool_correlation_signals([
            {
                "event": "batch_summary",
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "lane-a",
                                "provider_group": "pro-group",
                                "pool_identity": "pro-group/main/account",
                                "ok": True,
                                "artifact_metrics": {
                                    "artifact_count": 1,
                                    "valid_image_count": 0,
                                    "invalid_image_count": 1,
                                    "aspect_ratio_mismatch_count": 1,
                                },
                            },
                            {
                                "provider": "lane-b",
                                "provider_group": "pro-group",
                                "pool_identity": "pro-group/main/account",
                                "ok": True,
                                "artifact_metrics": {
                                    "artifact_count": 1,
                                    "valid_image_count": 0,
                                    "invalid_image_count": 1,
                                    "aspect_ratio_mismatch_count": 1,
                                },
                            },
                        ],
                    },
                },
            },
        ])

        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["failure_category"], "invalid_artifact")
        self.assertEqual(signals[0]["providers"], ["lane-a", "lane-b"])

    def test_pool_scores_group_attempts_by_pool_identity(self) -> None:
        scores = pool_scores([
            {
                "event": "batch_summary",
                "recorded_at": 9990,
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "gptge-key1",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": True,
                                "latency_ms": 20000,
                            },
                            {
                                "provider": "gptge-key2",
                                "provider_group": "gptge-group",
                                "pool_identity": "gptge-group/gptge-main/gptge-primary",
                                "ok": False,
                                "error_message": "HTTP Error 429: Too Many Requests",
                            },
                            {
                                "provider": "aiwave-key1",
                                "provider_group": "aiwave-group",
                                "pool_identity": "aiwave-group/aiwave-main/aiwave-primary",
                                "ok": True,
                                "latency_ms": 50000,
                            },
                        ],
                    }
                },
            },
            {
                "event": "batch_summary",
                "recorded_at": 0,
                "route_decisions": {
                    "old": {
                        "attempts": [
                            {
                                "provider": "old-key",
                                "provider_group": "old-group",
                                "pool_identity": "old-group/old/old",
                                "ok": True,
                                "latency_ms": 1,
                            },
                        ],
                    },
                },
            },
        ], window_seconds=3600, now=10000)

        rows = {row["pool_identity"]: row for row in scores}
        self.assertNotIn("old-group/old/old", rows)
        self.assertEqual(rows["gptge-group/gptge-main/gptge-primary"]["attempt_count"], 2)
        self.assertEqual(rows["gptge-group/gptge-main/gptge-primary"]["success_count"], 1)
        self.assertEqual(rows["gptge-group/gptge-main/gptge-primary"]["errors"], {"rate_limit": 1})
        self.assertEqual(rows["gptge-group/gptge-main/gptge-primary"]["providers"], ["gptge-key1", "gptge-key2"])
        self.assertEqual(rows["aiwave-group/aiwave-main/aiwave-primary"]["p50_latency_ms"], 50000)
        self.assertEqual(
            pool_status_counts(scores),
            {"ready_recent_success": 2},
        )

    def test_pool_scores_treat_invalid_artifacts_as_pool_failures(self) -> None:
        scores = pool_scores([
            {
                "event": "batch_summary",
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "lane-1",
                                "provider_group": "group",
                                "pool_identity": "group/station/account",
                                "ok": True,
                                "latency_ms": 1000,
                                "artifact_metrics": {
                                    "artifact_count": 1,
                                    "valid_image_count": 0,
                                    "invalid_image_count": 1,
                                    "aspect_ratio_mismatch_count": 1,
                                },
                            },
                        ],
                    },
                },
            },
        ])

        self.assertEqual(scores[0]["pool_identity"], "group/station/account")
        self.assertEqual(scores[0]["success_count"], 0)
        self.assertEqual(scores[0]["errors"], {"invalid_artifact": 1})
        self.assertEqual(scores[0]["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(pool_status_counts(scores), {"pool_risk_no_recent_success": 1})

    def test_pool_scores_ignore_subsecond_success_as_non_real_latency_sample(self) -> None:
        scores = pool_scores([
            {
                "event": "batch_summary",
                "route_decisions": {
                    "item-1": {
                        "attempts": [
                            {
                                "provider": "lane-1",
                                "provider_group": "group",
                                "pool_identity": "group/station/account",
                                "ok": True,
                                "latency_ms": 10,
                            },
                        ],
                    },
                },
            },
        ])

        self.assertEqual(scores[0]["success_count"], 0)
        self.assertEqual(scores[0]["ignored_fast_success_count"], 1)
        self.assertEqual(pool_status_counts(scores), {"pool_risk_no_recent_success": 1})


if __name__ == "__main__":
    unittest.main()
