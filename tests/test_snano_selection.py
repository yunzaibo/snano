from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.adapters.apiyi_gemini import ApiyiGeminiAdapter
from router.core.batch_runner import run_job_file
from router.core.models import GenerationRequest
from router.core.provider_registry import build_adapter
from router.core.routing_presets import resolve_routing_preset
from router.core.snano_selection import MODEL_LANES, select_snano_model

ROOT = Path(__file__).resolve().parents[1]


def request(**kwargs):
    return GenerationRequest(job_type="manifest", request_type="text_to_image",
                             profile="generic", prompt="test", **kwargs)


class SnanoSelectionTests(unittest.TestCase):
    def test_precedence_family_boundary_and_unknown_suffix(self):
        lanes = list(MODEL_LANES.values())
        self.assertEqual(select_snano_model(request(), lanes).model, "nano-banana-pro")
        self.assertEqual(select_snano_model(request(metadata={"snanoIntent": "professional"}), lanes).model,
                         "gemini-3-pro-image")
        for model in MODEL_LANES:
            selected = select_snano_model(request(metadata={"snanoModel": model, "snanoIntent": "professional"}), lanes)
            self.assertEqual(selected.model, model)
            self.assertEqual(selected.reason, "explicit_model")
        for model in ("gpt-image-2.5-flare-vip", "gemini-3-pro-image-preview-vip", "gemini-3-pro-image-preview-4k"):
            with self.assertRaises(ValueError):
                select_snano_model(request(metadata={"snanoModel": model}), lanes)
        with self.assertRaises(ValueError):
            select_snano_model(request(), ["apiyi-simage-gpt-image-2-5-flare"])

    def test_explicit_provider_and_conflict(self):
        lane = MODEL_LANES["gemini-3-pro-image-preview-c"]
        self.assertEqual(select_snano_model(request(), [lane]).provider, lane)
        with self.assertRaises(ValueError):
            select_snano_model(request(metadata={"snanoModel": "nano-banana-pro"}), [lane])

    def test_native_dimensions_and_no_auto_literal(self):
        adapter = ApiyiGeminiAdapter(api_key="test-key")
        for size in ("1K", "2K", "4K"):
            body = adapter.build_request(request(size=size, aspect_ratio="auto"))
            self.assertEqual(body["generationConfig"]["imageConfig"], {"imageSize": size})
        for kwargs in ({"size": "2048x2048"}, {"aspect_ratio": "17:8"}):
            with self.assertRaises(ValueError):
                adapter.build_request(request(**kwargs))

    def test_newapi_native_payloads_preserve_model_and_reference(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "MIR_SOURCES_CONFIG_FILE": str(ROOT / "configs/sources.apiyi.yaml"),
            "MIR_APIYI_API_KEY": "test-key",
        }):
            ref = Path(tmp) / "ref.png"
            ref.write_bytes(b"test-image")
            for model, lane in MODEL_LANES.items():
                adapter = build_adapter(lane)
                if model == "nano-banana-pro":
                    self.assertNotIsInstance(adapter, ApiyiGeminiAdapter)
                    self.assertEqual(adapter.endpoint_path, "/images/edits")
                    continue
                self.assertIsInstance(adapter, ApiyiGeminiAdapter)
                self.assertEqual(adapter._endpoint_url(), f"https://api.morewater.vip/v1beta/models/{model}:generateContent")
                body = adapter.build_request(request(reference_images=[str(ref)], size="4K", aspect_ratio="16:9"))
                self.assertEqual(body["contents"][0]["parts"][1]["inlineData"]["data"],
                                 base64.b64encode(b"test-image").decode())
                self.assertEqual(body["generationConfig"]["imageConfig"], {"imageSize": "4K", "aspectRatio": "16:9"})

    def test_mixed_batch_restricts_each_item_to_selected_model(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "MIR_SOURCES_CONFIG_FILE": str(ROOT / "configs/sources.apiyi.yaml"),
            "MIR_ROUTING_PRESETS_FILE": str(ROOT / "configs/routing-presets.yaml"),
        }):
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({"jobType": "manifest", "items": [
                {"id": model, "prompt": "test", "requestType": "text_to_image", "size": "1K",
                 "metadata": {"snanoModel": model}} for model in MODEL_LANES
            ]}))
            preset = resolve_routing_preset("snano")
            result = run_job_file(str(job), providers=preset.providers, routing_preset=preset,
                                  output_dir=str(root / "output"), batch_dir=str(root / "batches"),
                                  ledger_dir=str(root / "ledger"), perf_log_path=str(root / "perf.jsonl"),
                                  dry_run=True)
            summary = json.loads(Path(result.summary_path).read_text())
            self.assertEqual(summary["max_workers"], 4)
            for row in summary["items"]:
                self.assertEqual(row["planned_providers"], [MODEL_LANES[row["item_id"]]])
                self.assertEqual(row["request_contract"]["model_selection"]["model"], row["item_id"])


if __name__ == "__main__":
    unittest.main()
