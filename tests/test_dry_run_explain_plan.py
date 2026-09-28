from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.batch_runner import load_job_items, run_job_file
from router.core.routing_presets import resolve_routing_preset


class DryRunExplainPlanTests(unittest.TestCase):
    def test_simage_prescribed_dry_run_uses_shared_production_lanes(self) -> None:
        refs = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for idx in range(5):
                ref = root / f"ref-{idx}.png"
                ref.write_bytes(b"\x89PNG\r\n\x1a\nfake")
                refs.append(str(ref))
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": f"simage-{idx:03d}",
                        "requestType": "image_to_image",
                        "prompt": "same comparison task",
                        "aspectRatio": "auto",
                        "referenceImages": refs,
                        "metadata": {"requireFullReferenceLock": True},
                    }
                    for idx in range(8)
                ],
            }), encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(Path("configs/sources.shared.example.yaml").resolve()),
                "MIR_ROUTING_PRESETS_FILE": str(Path("configs/routing-presets.yaml").resolve()),
            }, clear=False):
                preset = resolve_routing_preset("simage")
                result = run_job_file(
                    str(job),
                    providers=preset.providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    dry_run=True,
                    preset_name="simage",
                    routing_preset=preset,
                    max_workers=8,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            self.assertEqual(summary["routing_preset"], "simage")
            self.assertIn("apiyi-simage-gpt-image-2", preset.providers)
            self.assertEqual(summary["max_workers"], 8)
            self.assertEqual(len(summary["items"]), 8)
            for item in summary["items"]:
                self.assertEqual(item["request_contract"]["size"], "2K")
                self.assertEqual(item["request_contract"]["reference_count"], 5)
                self.assertCountEqual(
                    item["planned_providers"],
                    ["apiyi-simage-gpt-image-2", "laozhang-simage-gpt-image-2", "apimart-gpt-image-2-i2i"],
                )

    def test_manifest_size_contract_comes_from_structured_fields_not_prompt_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": "prompt-only",
                        "requestType": "text_to_image",
                        "prompt": "请生成 4K 16:9 横图",
                    },
                    {
                        "id": "structured",
                        "requestType": "text_to_image",
                        "prompt": "请生成商品图",
                        "size": "4K",
                        "aspectRatio": "16:9",
                    },
                ],
            }), encoding="utf-8")

            _, items = load_job_items(str(job))
            by_id = {item_id: request for item_id, request, _ in items}

            self.assertIsNone(by_id["prompt-only"].size)
            self.assertIsNone(by_id["prompt-only"].aspect_ratio)
            self.assertEqual(by_id["structured"].size, "4K")
            self.assertEqual(by_id["structured"].aspect_ratio, "16:9")

            result = run_job_file(
                str(job),
                providers=["gptge"],
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                dry_run=True,
            )
            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            rows = {item["item_id"]: item for item in summary["items"]}

            self.assertIsNone(rows["prompt-only"]["request_contract"]["size"])
            self.assertIsNone(rows["prompt-only"]["request_contract"]["aspect_ratio"])
            self.assertEqual(rows["structured"]["request_contract"]["size"], "4K")
            self.assertEqual(rows["structured"]["request_contract"]["aspect_ratio"], "16:9")

    def test_dry_run_writes_policy_plan_without_calling_provider_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": "one",
                        "requestType": "text_to_image",
                        "prompt": "hello",
                        "routingPolicy": "speed_first",
                    }
                ],
            }), encoding="utf-8")

            result = run_job_file(
                str(job),
                providers=["gptge", "aiwave"],
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                dry_run=True,
            )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            ledger = json.loads(Path(result.ledger_path).read_text(encoding="utf-8"))

            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["schema_version"], "batch-run-summary/v1")
            self.assertEqual(result.success_count, 1)
            self.assertEqual(result.failure_count, 0)
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["failure_count"], 0)
            self.assertEqual(summary["items"][0]["status"], "planned")
            self.assertEqual(summary["items"][0]["routing_policy"], "speed_first")
            self.assertEqual(summary["items"][0]["effective_routing_mode"], "race")
            self.assertEqual(summary["items"][0]["planned_providers"], ["gptge", "aiwave"])
            self.assertEqual(ledger["schema_version"], "batch-ledger/v1")
            self.assertEqual(ledger["summary_schema_version"], "batch-run-summary/v1")

    def test_dry_run_explains_capability_and_probe_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": "one",
                        "requestType": "text_to_image",
                        "prompt": "hello",
                        "routingPolicy": "speed_first",
                    }
                ],
            }), encoding="utf-8")

            result = run_job_file(
                str(job),
                providers=["gptge", "aiwave"],
                output_dir=str(root / "artifacts"),
                batch_dir=str(root / "batches"),
                ledger_dir=str(root / "ledger"),
                dry_run=True,
            )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            item = summary["items"][0]

            self.assertEqual(item["capability_decisions"][0]["provider"], "gptge")
            self.assertEqual(item["capability_decisions"][0]["reason"], "supported")
            self.assertEqual(item["probe_decisions"][0]["probe_type"], "health_check")
            self.assertFalse(item["probe_decisions"][0]["real_image_generation"])

    def test_dry_run_reports_item_level_workers_and_policy_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = root / "sources.yaml"
            sources.write_text("""
schemaVersion: multi-source-config/v1
credentials:
  p1-key:
    secretRef:
      type: env
      name: MIR_TEST_P1_KEY
  p2-key:
    secretRef:
      type: env
      name: MIR_TEST_P2_KEY
  p3-key:
    secretRef:
      type: env
      name: MIR_TEST_P3_KEY
lanes:
  p1:
    provider: p1
    providerGroup: group-1
    sourceTier: professional
    credential: p1-key
    model: model-1
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 6
    priority: 10
    enabled: true
  p2:
    provider: p2
    providerGroup: group-2
    sourceTier: professional
    credential: p2-key
    model: model-2
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 6
    priority: 20
    enabled: true
  p3:
    provider: p3
    providerGroup: group-3
    sourceTier: professional
    credential: p3-key
    model: model-3
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 6
    priority: 30
    enabled: true
""", encoding="utf-8")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": f"item-{idx:03d}",
                        "requestType": "text_to_image",
                        "prompt": f"dry-run item {idx}",
                        "routingPolicy": "load_balance",
                    }
                    for idx in range(6)
                ],
            }), encoding="utf-8")

            with patch.dict(os.environ, {"MIR_SOURCES_CONFIG_FILE": str(sources)}, clear=False):
                result = run_job_file(
                    str(job),
                    providers=["p1", "p2", "p3"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    dry_run=True,
                    max_workers=6,
                    routing_policy="load_balance",
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            items = summary["items"]

            self.assertTrue(summary["dry_run"])
            self.assertEqual(summary["max_workers"], 6)
            self.assertEqual(summary["success_count"], 6)
            self.assertEqual(len(items), 6)
            self.assertTrue(all(item["status"] == "planned" for item in items))
            self.assertEqual(
                [item["planned_providers"][0] for item in items],
                ["p1", "p2", "p3", "p1", "p2", "p3"],
            )
            for item in items:
                self.assertEqual(item["routing_policy"], "load_balance")
                self.assertEqual(item["effective_routing_mode"], "fallback")
                self.assertEqual(item["policy_plan"]["provider_order"], item["planned_providers"])
                self.assertEqual(len(item["capability_decisions"]), 3)
                self.assertEqual(len(item["probe_decisions"]), 3)
                self.assertTrue(all(decision["reason"] == "supported" for decision in item["capability_decisions"]))
                self.assertTrue(
                    all(decision["real_image_generation"] is False for decision in item["probe_decisions"])
                )

    def test_cli_explain_plan_outputs_json_without_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {
                        "id": "one",
                        "requestType": "text_to_image",
                        "prompt": "hello",
                        "routingPolicy": "speed_first",
                    }
                ],
            }), encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "router.main",
                    "explain-plan",
                    str(job),
                    "--provider",
                    "gptge",
                    "--batch-dir",
                    str(root / "batches"),
                    "--ledger-dir",
                    str(root / "ledger"),
                    "--output-dir",
                    str(root / "artifacts"),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads(completed.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["dry_run"])
            self.assertEqual(payload["success_count"], 1)
            self.assertEqual(payload["failure_count"], 0)
            self.assertEqual(payload["items"][0]["status"], "planned")


if __name__ == "__main__":
    unittest.main()
