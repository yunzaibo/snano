from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core.batch_runner import run_job_file
from router.core.models import GenerationRequest
from router.core.routing_presets import resolve_routing_preset
from router.core.simage_selection import select_simage_model


FLARE = "apiyi-simage-gpt-image-2-5-flare"
SUNBURST = "apiyi-simage-gpt-image-2-5-sunburst"


def request(*, refs: list[str] | None = None, metadata: dict | None = None) -> GenerationRequest:
    return GenerationRequest(
        job_type="manifest",
        request_type="image_to_image" if refs else "text_to_image",
        profile="simage-professional-2k",
        prompt="test",
        reference_images=refs or [],
        metadata=metadata or {},
    )


class SimageSelectionTests(unittest.TestCase):
    def test_default_and_speed_choose_flare(self) -> None:
        self.assertEqual(select_simage_model(request(), [FLARE, SUNBURST]).provider, FLARE)
        speed = select_simage_model(request(metadata={"simageIntent": "speed"}), [FLARE, SUNBURST])
        self.assertEqual(speed.model, "gpt-image-2.5-flare-vip")
        self.assertEqual(speed.reason, "speed_preference")

    def test_reference_precision_chooses_sunburst(self) -> None:
        selected = select_simage_model(
            request(refs=["reference.png"], metadata={"simageIntent": "precision"}),
            [FLARE, SUNBURST],
        )
        self.assertEqual(selected.provider, SUNBURST)
        self.assertEqual(selected.model, "gpt-image-2.5-sunburst-vip")
        self.assertEqual(selected.reason, "reference_precision_edit")

    def test_explicit_model_wins_over_intent_and_accepts_full_id(self) -> None:
        for value in ("sunburst", "gpt-image-2.5-sunburst-vip"):
            selected = select_simage_model(
                request(metadata={"simageModel": value, "simageIntent": "speed"}),
                [FLARE, SUNBURST],
            )
            self.assertEqual(selected.provider, SUNBURST)
            self.assertEqual(selected.reason, "explicit_model")

    def test_unknown_aliases_and_provider_conflicts_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "simageModel"):
            select_simage_model(request(metadata={"simageModel": "all"}), [FLARE, SUNBURST])
        with self.assertRaisesRegex(ValueError, "conflicts"):
            select_simage_model(request(metadata={"simageModel": "sunburst"}), [FLARE])

    def test_simage_preset_plans_one_selected_lane_per_item(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "reference.png"
            ref.write_bytes(b"reference")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "ordinary", "requestType": "text_to_image", "prompt": "ordinary"},
                    {
                        "id": "precision",
                        "requestType": "image_to_image",
                        "prompt": "edit",
                        "referenceImages": [str(ref)],
                        "metadata": {"simageIntent": "precision"},
                    },
                ],
            }), encoding="utf-8")
            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(Path("configs/sources.apiyi.yaml").resolve()),
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
                    max_workers=2,
                )
            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            rows = {row["item_id"]: row for row in summary["items"]}
            self.assertEqual(rows["ordinary"]["planned_providers"], [FLARE])
            self.assertEqual(rows["ordinary"]["request_contract"]["model_selection"]["reason"], "default_generation")
            self.assertEqual(rows["precision"]["planned_providers"], [SUNBURST])
            self.assertEqual(rows["precision"]["request_contract"]["model_selection"]["model"], "gpt-image-2.5-sunburst-vip")


if __name__ == "__main__":
    unittest.main()
