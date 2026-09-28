from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core import batch_runner
from router.core.batch_runner import run_job_file
from router.core.models import CapabilityBucket, GenerationRequest, GenerationResult, ProviderCapability


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeAdapter:
    def __init__(self, provider: str, results: list[GenerationResult] | None = None):
        self.provider = provider
        self.results = list(results or [])
        self.calls = 0

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider,
            bucket=CapabilityBucket.MULTI_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=True,
            max_reference_images=5,
            max_concurrency=1,
            enabled=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return GenerationResult(
            ok=True,
            provider=self.provider,
            model="fake-model",
            request_type=request.request_type,
            reference_count=len(request.reference_images),
            artifact_blobs=[PNG_1X1],
            provider_response={"ok": True},
        )


def failed(provider: str, message: str) -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code="HTTPError",
        error_message=message,
    )


class BatchRunnerStatePersistenceTests(unittest.TestCase):
    def test_provider_cooldown_persists_and_next_run_skips_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "hello"}],
            }), encoding="utf-8")
            state_path = root / "provider-health.json"

            p1_first = FakeAdapter("p1", [failed("p1", "HTTP Error 429: Too Many Requests")])
            p2_first = FakeAdapter("p2")
            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": p1_first, "p2": p2_first}, {})):
                first = run_job_file(
                    str(job),
                    providers=["p1", "p2"],
                    output_dir=str(root / "artifacts-1"),
                    batch_dir=str(root / "batches-1"),
                    ledger_dir=str(root / "ledger"),
                    provider_state_path=str(state_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(first.success_count, 1)
            self.assertTrue(state_path.exists())

            p1_second = FakeAdapter("p1")
            p2_second = FakeAdapter("p2")
            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": p1_second, "p2": p2_second}, {})):
                second = run_job_file(
                    str(job),
                    providers=["p1", "p2"],
                    output_dir=str(root / "artifacts-2"),
                    batch_dir=str(root / "batches-2"),
                    ledger_dir=str(root / "ledger"),
                    provider_state_path=str(state_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            summary = json.loads(Path(second.summary_path).read_text(encoding="utf-8"))
            self.assertEqual(second.success_count, 1)
            self.assertEqual(p1_second.calls, 0)
            self.assertEqual(summary["items"][0]["skipped_providers"][0]["provider"], "p1")
            self.assertEqual(summary["provider_state_path"], str(state_path))
            self.assertIn("provider_health", summary)
            self.assertEqual(summary["recovery_events"][0]["provider"], "p1")
            self.assertEqual(summary["recovery_events"][0]["action"], "operator_check_quota")
            self.assertFalse(summary["recovery_events"][0]["auto_execute"])


if __name__ == "__main__":
    unittest.main()
