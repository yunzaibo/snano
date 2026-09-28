from __future__ import annotations

import unittest

from router.core.runtime_config import RuntimeConfig


class RuntimeConfigTests(unittest.TestCase):
    def test_default_provider_state_path_is_local_data_file(self) -> None:
        config = RuntimeConfig.default()

        self.assertTrue(config.provider_state_path.endswith("data/provider-health.json"))
        self.assertTrue(config.perf_log_path.endswith("logs/performance.jsonl"))
        self.assertEqual(config.max_retries_per_provider, 1)
        self.assertEqual(config.retry_delay_seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
