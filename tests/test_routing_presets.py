from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from router.core.routing_presets import load_routing_presets, resolve_routing_preset
from router.core.batch_runner import run_job_file
from router.core.scheduler import ProviderState
from router.core import batch_runner
from tests.test_batch_runner_health import FakeAdapter, success_result, success_result_with_size


SOURCE_CONFIG = """
schemaVersion: multi-source-config/v1
stations:
  apiyi-main:
    kind: relay
    baseUrl: https://api.apiyi.com/v1
    vendorGroup: apiyi-group
    sourceTier: professional
    enabled: true
  laozhang-main:
    kind: relay
    baseUrl: https://api.laozhang.ai/v1
    vendorGroup: laozhang-group
    sourceTier: professional
    enabled: true
accounts:
  apiyi-primary:
    station: apiyi-main
    accountGroup: apiyi-primary
    enabled: true
  laozhang-primary:
    station: laozhang-main
    accountGroup: laozhang-primary
    enabled: true
credentials:
  apiyi-key:
    account: apiyi-primary
    secretRef:
      type: env
      name: MIR_APIYI_API_KEY
    authScheme: bearer
    enabled: true
  laozhang-key:
    account: laozhang-primary
    secretRef:
      type: env
      name: MIR_LAOZHANG_API_KEY
    authScheme: bearer
    enabled: true
routes:
  openai-images:
    adapterFamily: openai_images
    path: /images/generations
    enabled: true
lanes:
  apiyi-primary-openai-images:
    provider: apiyi
    providerGroup: apiyi-group
    sourceTier: professional
    station: apiyi-main
    account: apiyi-primary
    credential: apiyi-key
    route: openai-images
    model: gemini-3-pro-image-preview-4k
    capabilityBucket: T
    maxReferenceImages: 0
    priority: 10
    enabled: true
  laozhang-openclaw-openai-images:
    provider: laozhang
    providerGroup: laozhang-group
    sourceTier: professional
    station: laozhang-main
    account: laozhang-primary
    credential: laozhang-key
    route: openai-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    maxReferenceImages: 0
    priority: 20
    enabled: true
"""


PRESET_CONFIG = """
schemaVersion: routing-presets/v1
presets:
  pro_primary:
    providers:
      - apiyi-primary-openai-images
      - laozhang-openclaw-openai-images
    routingPolicy: fallback
    providerTier: professional
    maxRetriesPerProvider: 1
    springBackSeconds: 300
    springBackProbeEvery: 2
  strict_4k:
    providers:
      - small-provider
      - large-provider
    routingPolicy: fallback
    providerTier: professional
    maxRetriesPerProvider: 0
    artifactMinWidth: 4096
    artifactMinHeight: 4096
"""


class RoutingPresetTests(unittest.TestCase):
    def test_loads_preset_without_secret_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "routing-presets.yaml"
            path.write_text(PRESET_CONFIG, encoding="utf-8")
            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(path)}, clear=False):
                preset = resolve_routing_preset("pro_primary")
                serialized = json.dumps({name: asdict(row) for name, row in load_routing_presets().items()})

            self.assertIsNotNone(preset)
            self.assertEqual(preset.providers[0], "apiyi-primary-openai-images")
            self.assertEqual(preset.routing_policy, "fallback")
            self.assertEqual(preset.spring_back_seconds, 300)
            self.assertEqual(preset.spring_back_probe_every, 2)
            self.assertNotIn("sk-", serialized)

    def test_internal_snano_and_simage_presets_use_professional_2k_pools(self) -> None:
        config = Path("configs/routing-presets.yaml").resolve()
        with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(config)}, clear=False):
            snano = resolve_routing_preset("snano")
            snano_race = resolve_routing_preset("snano_race")
            apiyi_only = resolve_routing_preset("snano_apiyi_only")
            snano_legacy = resolve_routing_preset("snano_professional_4k")
            professional_i2i = resolve_routing_preset("professional_i2i_single_ref")
            laozhang_i2i = resolve_routing_preset("safe_laozhang_nano_banana_pro_4k_i2i")
            rightcodes_i2i = resolve_routing_preset("safe_rightcodes_nano_banana_pro_i2i")
            apimart_i2i = resolve_routing_preset("safe_apimart_gemini_i2i")
            apimart_gpt_i2i = resolve_routing_preset("safe_apimart_gpt_image_2_i2i")
            apiyi_simage = resolve_routing_preset("safe_apiyi_simage_image2_oss")
            laozhang_simage = resolve_routing_preset("safe_laozhang_simage_gpt_image_2")
            simage = resolve_routing_preset("simage")
            simage_2k = resolve_routing_preset("simage_professional_2k")
            serialized = json.dumps({name: asdict(row) for name, row in load_routing_presets().items()})

        expected_snano = [
            "apiyi-nano-banana-pro-4k",
            "laozhang-nano-banana-pro-4k-i2i",
            "apimart-gemini-i2i",
        ]
        self.assertEqual(snano.providers, expected_snano)
        self.assertEqual(snano_legacy.providers, expected_snano)
        self.assertEqual(apiyi_only.providers, ["apiyi-nano-banana-pro-4k"])
        self.assertEqual(
            professional_i2i.providers,
            [
                "apiyi-nano-banana-pro-4k",
                "laozhang-nano-banana-pro-4k-i2i",
                "apimart-gemini-i2i",
            ],
        )
        self.assertEqual(professional_i2i.routing_policy, "load_balance")
        self.assertEqual(laozhang_i2i.providers, ["laozhang-nano-banana-pro-4k-i2i"])
        self.assertEqual(rightcodes_i2i.providers, ["rightcodes-nano-banana-pro-i2i"])
        self.assertEqual(apimart_i2i.providers, ["apimart-gemini-i2i"])
        self.assertEqual(apimart_gpt_i2i.providers, ["apimart-gpt-image-2-i2i"])
        self.assertEqual(apiyi_simage.providers, ["apiyi-simage-gpt-image-2"])
        self.assertEqual(laozhang_simage.providers, ["laozhang-simage-gpt-image-2"])
        self.assertEqual(snano.routing_policy, "health_aware_load_balance")
        self.assertEqual(snano_race.routing_policy, "speed_first")
        self.assertEqual(snano_race.providers, expected_snano)
        self.assertEqual(snano.provider_tier, "professional")
        self.assertEqual(snano.default_size, "2K")
        self.assertEqual(snano.artifact_min_width, 2048)
        self.assertEqual(snano.artifact_min_height, 2048)
        self.assertEqual(snano.artifact_dimension_mode, "long_edge")
        self.assertEqual(snano_legacy.default_size, "4K")
        self.assertEqual(snano_legacy.artifact_min_width, 4096)
        self.assertEqual(
            simage.providers,
            [
                "apiyi-simage-gpt-image-2",
                "laozhang-simage-gpt-image-2",
                "apimart-gpt-image-2-i2i",
            ],
        )
        self.assertEqual(simage_2k.providers, simage.providers)
        self.assertEqual(simage.routing_policy, "health_aware_load_balance")
        self.assertEqual(simage.provider_tier, "professional")
        self.assertEqual(simage.default_size, "2K")
        self.assertEqual(simage.artifact_min_width, 2048)
        self.assertEqual(simage.artifact_dimension_mode, "long_edge")
        self.assertNotIn("sk-", serialized)

    def test_cli_explain_plan_expands_preset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = root / "sources.yaml"
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            source_config.write_text(SOURCE_CONFIG, encoding="utf-8")
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "hello"}],
            }), encoding="utf-8")

            env = {
                **os.environ,
                "MIR_SOURCES_CONFIG_FILE": str(source_config),
                "MIR_ROUTING_PRESETS_FILE": str(preset_config),
                "MIR_APIYI_API_KEY": "secret-apiyi-key",
                "MIR_LAOZHANG_API_KEY": "secret-laozhang-key",
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "router.main",
                    "explain-plan",
                    str(job),
                    "--preset",
                    "pro_primary",
                    "--batch-dir",
                    str(root / "batches"),
                    "--ledger-dir",
                    str(root / "ledger"),
                    "--output-dir",
                    str(root / "artifacts"),
                    "--perf-log-file",
                    str(root / "perf.jsonl"),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )

            payload = json.loads(completed.stdout)
            summary = json.loads(Path(payload["summary_path"]).read_text(encoding="utf-8"))

            self.assertEqual(payload["routing_preset"], "pro_primary")
            self.assertEqual(summary["routing_preset"], "pro_primary")
            self.assertEqual(payload["items"][0]["routing_policy"], "fallback")
            self.assertEqual(
                payload["items"][0]["planned_providers"],
                ["apiyi-primary-openai-images", "laozhang-openclaw-openai-images"],
            )
            self.assertNotIn("secret-apiyi-key", completed.stdout)
            self.assertNotIn("secret-laozhang-key", completed.stdout)

    def test_spring_back_defers_primary_for_non_probe_batch_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = root / "sources.yaml"
            preset_config = root / "routing-presets.yaml"
            state_path = root / "provider-health.json"
            job = root / "job.json"
            source_config.write_text(SOURCE_CONFIG, encoding="utf-8")
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                ],
            }), encoding="utf-8")

            expired_cooldown = time.time() - 60
            state_path.write_text(json.dumps({
                "schema_version": "provider-health/v1",
                "providers": {
                    "apiyi-primary-openai-images": {
                        "healthy": True,
                        "cooldown_until": expired_cooldown,
                        "cooldown_reason": "rate_limit_or_quota",
                        "last_error_category": "rate_limit",
                        "last_failure_at": expired_cooldown - 60,
                        "last_success_at": None,
                        "consecutive_failures": 1,
                    }
                },
            }), encoding="utf-8")

            adapters = {
                "apiyi-primary-openai-images": FakeAdapter(
                    "apiyi-primary-openai-images",
                    [success_result("apiyi-primary-openai-images")],
                ),
                "laozhang-openclaw-openai-images": FakeAdapter(
                    "laozhang-openclaw-openai-images",
                    [success_result("laozhang-openclaw-openai-images")],
                ),
            }
            env = {
                "MIR_SOURCES_CONFIG_FILE": str(source_config),
                "MIR_ROUTING_PRESETS_FILE": str(preset_config),
                "MIR_APIYI_API_KEY": "secret-apiyi-key",
                "MIR_LAOZHANG_API_KEY": "secret-laozhang-key",
            }
            with patch.dict(os.environ, env, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("pro_primary")
                result = run_job_file(
                    str(job),
                    providers=preset.providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    provider_state_path=str(state_path),
                    max_retries_per_provider=1,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))

            self.assertEqual(result.success_count, 2)
            self.assertEqual(result.item_results[0].selected_provider, "apiyi-primary-openai-images")
            self.assertEqual(result.item_results[1].selected_provider, "laozhang-openclaw-openai-images")
            self.assertEqual(
                summary["items"][1]["skipped_providers"][0]["reason"],
                "spring_back_deferred",
            )

    def test_preset_min_artifact_dimensions_force_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "hello"}],
            }), encoding="utf-8")
            adapters = {
                "small-provider": FakeAdapter("small-provider", [success_result("small-provider")]),
                "large-provider": FakeAdapter(
                    "large-provider",
                    [success_result_with_size("large-provider", 4096, 4096)],
                ),
            }

            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(preset_config)}, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("strict_4k")
                result = run_job_file(
                    str(job),
                    providers=preset.providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.item_results[0].selected_provider, "large-provider")
        self.assertEqual(result.item_results[0].attempts[0]["error_category"], "invalid_artifact")
        self.assertEqual(result.item_results[0].attempts[0]["artifact_metrics"]["dimension_mismatch_count"], 1)

    def test_preset_4k_accepts_human_language_4k_landscape_by_long_edge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{
                    "id": "one",
                    "requestType": "text_to_image",
                    "prompt": "hello",
                    "aspectRatio": "16:9",
                }],
            }), encoding="utf-8")
            adapters = {
                "large-provider": FakeAdapter(
                    "large-provider",
                    [success_result_with_size("large-provider", 4096, 2304)],
                ),
            }

            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(preset_config)}, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("strict_4k")
                result = run_job_file(
                    str(job),
                    providers=["large-provider"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

        self.assertEqual(result.success_count, 1)
        self.assertEqual(result.item_results[0].status, "success")
        metrics = result.item_results[0].attempts[0]["artifact_metrics"]
        self.assertEqual(metrics["dimension_mismatch_count"], 0)
        self.assertEqual(metrics["dimension_contract_mode"], "long_edge")

    def test_preset_4k_allows_square_4k_when_landscape_ratio_is_not_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{
                    "id": "one",
                    "requestType": "text_to_image",
                    "prompt": "hello",
                    "aspectRatio": "16:9",
                }],
            }), encoding="utf-8")
            adapters = {
                "large-provider": FakeAdapter(
                    "large-provider",
                    [success_result_with_size("large-provider", 4096, 4096)],
                ),
            }

            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(preset_config)}, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("strict_4k")
                result = run_job_file(
                    str(job),
                    providers=["large-provider"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

        self.assertEqual(result.success_count, 1)
        metrics = result.item_results[0].attempts[0]["artifact_metrics"]
        self.assertEqual(metrics["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(metrics["dimension_mismatch_count"], 0)
        self.assertTrue(metrics["images"][0]["human_4k_matches"])
        self.assertFalse(metrics["images"][0]["soft_aspect_ratio_matches"])

    def test_preset_4k_rejects_square_4k_when_landscape_ratio_is_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{
                    "id": "one",
                    "requestType": "text_to_image",
                    "prompt": "hello",
                    "aspectRatio": "16:9",
                    "metadata": {
                        "artifactAspectStrict": True,
                    },
                }],
            }), encoding="utf-8")
            adapters = {
                "large-provider": FakeAdapter(
                    "large-provider",
                    [success_result_with_size("large-provider", 4096, 4096)],
                ),
            }

            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(preset_config)}, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("strict_4k")
                result = run_job_file(
                    str(job),
                    providers=["large-provider"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

        self.assertEqual(result.failure_count, 1)
        metrics = result.item_results[0].attempts[0]["artifact_metrics"]
        self.assertEqual(metrics["aspect_ratio_mismatch_count"], 1)
        self.assertEqual(metrics["dimension_mismatch_count"], 1)
        self.assertFalse(metrics["images"][0]["human_4k_matches"])

    def test_preset_4k_rejects_small_landscape_even_when_ratio_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preset_config = root / "routing-presets.yaml"
            job = root / "job.json"
            preset_config.write_text(PRESET_CONFIG, encoding="utf-8")
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{
                    "id": "one",
                    "requestType": "text_to_image",
                    "prompt": "hello",
                    "aspectRatio": "16:9",
                }],
            }), encoding="utf-8")
            adapters = {
                "large-provider": FakeAdapter(
                    "large-provider",
                    [success_result_with_size("large-provider", 2048, 1152)],
                ),
            }

            with patch.dict(os.environ, {"MIR_ROUTING_PRESETS_FILE": str(preset_config)}, clear=False), patch.object(
                batch_runner,
                "_build_adapters",
                return_value=(adapters, {}),
            ):
                preset = resolve_routing_preset("strict_4k")
                result = run_job_file(
                    str(job),
                    providers=["large-provider"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                    routing_policy=preset.routing_policy,
                    provider_tier=preset.provider_tier,
                    preset_name=preset.name,
                    routing_preset=preset,
                )

        self.assertEqual(result.failure_count, 1)
        metrics = result.item_results[0].attempts[0]["artifact_metrics"]
        self.assertEqual(metrics["aspect_ratio_mismatch_count"], 0)
        self.assertEqual(metrics["dimension_mismatch_count"], 1)
        self.assertFalse(metrics["images"][0]["human_4k_matches"])


if __name__ == "__main__":
    unittest.main()
