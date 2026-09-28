from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core import batch_runner
from router.core.artifacts import write_result
from router.core.batch_runner import run_job_file
from router.core.models import CapabilityBucket, GenerationRequest, GenerationResult, ProviderCapability


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


class FakeAdapter:
    def __init__(self, provider: str, result: GenerationResult | list[GenerationResult]):
        self.provider = provider
        self.results = result if isinstance(result, list) else [result]

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider,
            bucket=CapabilityBucket.SINGLE_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=False,
            max_reference_images=1,
            enabled=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        return self.results.pop(0)


def request() -> GenerationRequest:
    return GenerationRequest(
        job_type="manifest",
        request_type="text_to_image",
        profile="generic",
        prompt="draw a small robot",
    )


def ratio_request(aspect_ratio: str) -> GenerationRequest:
    return GenerationRequest(
        job_type="manifest",
        request_type="text_to_image",
        profile="generic",
        prompt="draw a small robot",
        aspect_ratio=aspect_ratio,
    )


def image_request(reference_images: list[str]) -> GenerationRequest:
    return GenerationRequest(
        job_type="manifest",
        request_type="image_to_image",
        profile="generic",
        prompt="use the reference",
        reference_images=reference_images,
    )


def result(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_blobs=[PNG_1X1],
        provider_response={"ok": True},
        latency_ms=123,
        timings_ms={"request_build_ms": 3, "provider_http_ms": 100, "response_parse_ms": 2, "total_ms": 123},
    )


def png_result(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_blobs=[PNG_1X1],
        provider_response={"ok": True},
        latency_ms=123,
    )


def failure(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code="HTTPError",
        error_message="HTTP Error 429: Too Many Requests",
        latency_ms=456,
    )


def async_pending(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code="no_image",
        error_message="No image data found in provider response.",
        provider_response={
            "status": "queued",
            "task_id": "task-secret-should-not-be-logged",
        },
        latency_ms=321,
    )


def invalid_artifact_result(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_blobs=[b"not an image"],
        provider_response={"ok": True},
        latency_ms=99,
    )


class PerfLogTests(unittest.TestCase):
    def test_write_result_appends_generation_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            perf_log_path = Path(tmp) / "logs" / "performance.jsonl"
            output_dir = Path(tmp) / "artifacts"

            written = write_result(
                str(output_dir),
                "fake-provider",
                request(),
                result("fake-provider"),
                perf_log_path=str(perf_log_path),
            )

            lines = perf_log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)
            payload = json.loads(lines[0])
            self.assertEqual(payload["event"], "generation_result")
            self.assertEqual(payload["provider"], "fake-provider")
            self.assertEqual(payload["pool_identity"], "fake-provider")
            self.assertEqual(payload["latency_ms"], 123)
            self.assertEqual(payload["timings_ms"]["provider_http_ms"], 100)
            self.assertEqual(payload["artifact_count"], len(written.artifact_paths))
            self.assertEqual(payload["artifact_metrics"]["artifact_count"], 1)
            self.assertEqual(payload["artifact_metrics"]["valid_image_count"], 1)

            meta_path = Path(written.artifact_paths[0]).parent / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self.assertEqual(meta["pool_identity"], "fake-provider")
            self.assertEqual(meta["timings_ms"]["request_build_ms"], 3)
            self.assertEqual(meta["artifact_metrics"]["formats"], ["png"])

    def test_write_result_records_valid_image_artifact_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            perf_log_path = Path(tmp) / "logs" / "performance.jsonl"

            written = write_result(
                str(Path(tmp) / "artifacts"),
                "fake-provider",
                request(),
                png_result("fake-provider"),
                perf_log_path=str(perf_log_path),
            )

            payload = json.loads(perf_log_path.read_text(encoding="utf-8").strip())
            meta = json.loads((Path(written.artifact_paths[0]).parent / "meta.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["artifact_metrics"]["valid_image_count"], 1)
            self.assertEqual(payload["artifact_metrics"]["max_width"], 1)
            self.assertEqual(meta["artifact_metrics"]["images"][0]["format"], "png")

    def test_write_result_records_requested_aspect_ratio_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            perf_log_path = Path(tmp) / "logs" / "performance.jsonl"

            written = write_result(
                str(Path(tmp) / "artifacts"),
                "fake-provider",
                ratio_request("2:3"),
                png_result("fake-provider"),
                perf_log_path=str(perf_log_path),
            )

            payload = json.loads(perf_log_path.read_text(encoding="utf-8").strip())
            meta = json.loads((Path(written.artifact_paths[0]).parent / "meta.json").read_text(encoding="utf-8"))

            self.assertEqual(payload["artifact_metrics"]["expected_aspect_ratio"], "2:3")
            self.assertEqual(payload["artifact_metrics"]["aspect_ratio_mismatch_count"], 1)
            self.assertEqual(meta["artifact_metrics"]["aspect_ratio_mismatch_count"], 1)

    def test_write_result_records_lane_pool_identity_without_secret_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "sources.yaml"
            config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  pro-main:
    kind: relay
    baseUrl: https://api.example.invalid
    vendorGroup: pro-group
accounts:
  pro-account:
    station: pro-main
credentials:
  pro-credential:
    account: pro-account
    secretRef:
      type: env
      name: MIR_TEST_PRO_KEY
routes:
  pro-images:
    station: pro-main
    adapterFamily: openai_images
    path: /v1/images/generations
lanes:
  pro-images-lane:
    provider: laozhang
    providerGroup: pro-group
    station: pro-main
    account: pro-account
    credential: pro-credential
    route: pro-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    enabled: true
""", encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(config),
                "MIR_TEST_PRO_KEY": "redaction-sentinel-not-in-result",
            }, clear=False):
                written = write_result(
                    str(root / "artifacts"),
                    "pro-images-lane",
                    request(),
                    result("pro-images-lane"),
                    perf_log_path=str(perf_log_path),
                )

            payload = json.loads(perf_log_path.read_text(encoding="utf-8").strip())
            meta = json.loads((Path(written.artifact_paths[0]).parent / "meta.json").read_text(encoding="utf-8"))
            serialized = json.dumps({"payload": payload, "meta": meta}, ensure_ascii=False)

            self.assertNotIn("redaction-sentinel-not-in-result", serialized)
            self.assertEqual(payload["pool_identity"], "pro-group/pro-main/pro-account")
            self.assertEqual(payload["station_id"], "pro-main")
            self.assertEqual(payload["credential_id"], "pro-credential")
            self.assertEqual(meta["pool_identity"], "pro-group/pro-main/pro-account")
            self.assertEqual(meta["route_id"], "pro-images")

    def test_write_result_records_reference_image_size_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.png"
            ref.write_bytes(b"12345")
            perf_log_path = root / "logs" / "performance.jsonl"

            written = write_result(
                str(root / "artifacts"),
                "fake-provider",
                image_request([str(ref)]),
                result("fake-provider"),
                perf_log_path=str(perf_log_path),
            )

            meta_path = Path(written.artifact_paths[0]).parent / "meta.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            event = json.loads(perf_log_path.read_text(encoding="utf-8").strip())

            self.assertEqual(meta["reference_metrics"]["total_bytes"], 5)
            self.assertEqual(meta["reference_metrics"]["estimated_base64_bytes"], 8)
            self.assertEqual(event["reference_metrics"]["total_bytes"], 5)
            self.assertEqual(event["reference_metrics"]["estimated_base64_bytes"], 8)

    def test_run_job_file_appends_batch_summary_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "first"},
                ],
            }), encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"
            adapter = FakeAdapter("p1", result("p1"))

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": adapter}, {})):
                run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            lines = [json.loads(line) for line in perf_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(lines[0]["event"], "generation_result")
            self.assertEqual(lines[-1]["event"], "batch_summary")
            self.assertEqual(lines[-1]["success_count"], 1)
            self.assertEqual(lines[-1]["items_total"], 1)
            self.assertIn("route_decisions", lines[-1])

    def test_batch_summary_event_explains_fallback_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "first"},
                ],
            }), encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"
            p1 = FakeAdapter("p1", failure("p1"))
            p2 = FakeAdapter("p2", result("p2"))

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": p1, "p2": p2}, {})):
                run_job_file(
                    str(job),
                    providers=["p1", "p2"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            lines = [json.loads(line) for line in perf_log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            batch_event = lines[-1]
            route = batch_event["route_decisions"]["one"]
            self.assertEqual(route["selected_provider"], "p2")
            self.assertEqual(route["selected_provider_pool_identity"], "p2")
            self.assertEqual(route["attempted_providers"], ["p1", "p2"])
            self.assertEqual(route["attempts"][0]["provider"], "p1")
            self.assertEqual(route["attempts"][0]["pool_identity"], "p1")
            self.assertEqual(route["attempts"][0]["error_category"], "rate_limit")
            self.assertEqual(route["attempts"][0]["remediation_hint"], "p1_rate_limited_cooldown_and_try_independent_lane")
            self.assertEqual(batch_event["provider_attempt_totals"], {"p1": 1, "p2": 1})
            self.assertEqual(batch_event["pool_attempt_totals"], {"p1": 1, "p2": 1})
            self.assertEqual(batch_event["provider_artifact_summary"]["p2"]["artifact_count"], 1)
            self.assertEqual(batch_event["provider_artifact_summary"]["p2"]["invalid_image_count"], 0)
            self.assertEqual(batch_event["provider_artifact_summary"]["p2"]["aspect_ratio_mismatch_count"], 0)
            self.assertEqual(batch_event["pool_artifact_summary"]["p2"]["valid_image_count"], 1)
            self.assertEqual(batch_event["pool_error_category_totals"]["p1"]["rate_limit"], 1)
            self.assertEqual(batch_event["pool_error_category_totals"]["p2"]["success"], 1)
            self.assertEqual(batch_event["pool_outcome_summary"]["p1"]["status"], "pool_risk_no_recent_success")
            self.assertEqual(batch_event["pool_outcome_summary"]["p2"]["success_rate"], 1.0)
            self.assertEqual(
                batch_event["pool_outcome_status_counts"],
                {"pool_risk_no_recent_success": 1, "ready_recent_success": 1},
            )
            self.assertEqual(batch_event["pool_latency_summary"]["p2"]["avg_ms"], 123)
            self.assertEqual(batch_event["provider_queue_wait_summary"]["p1"]["count"], 1)
            self.assertEqual(batch_event["pool_queue_wait_summary"]["p2"]["count"], 1)
            self.assertEqual(batch_event["error_category_totals"]["rate_limit"], 1)

    def test_batch_summary_records_async_pending_contract_without_task_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "first"},
                ],
            }), encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"
            p1 = FakeAdapter("p1", async_pending("p1"))
            p2 = FakeAdapter("p2", result("p2"))

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": p1, "p2": p2}, {})):
                run_job_file(
                    str(job),
                    providers=["p1", "p2"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log_path),
                    max_workers=1,
                    max_retries_per_provider=1,
                    retry_delay_seconds=0,
                )

            serialized = perf_log_path.read_text(encoding="utf-8")
            lines = [json.loads(line) for line in serialized.splitlines() if line.strip()]
            first_generation = lines[0]
            batch_event = lines[-1]
            attempt = batch_event["route_decisions"]["one"]["attempts"][0]

            self.assertEqual(first_generation["provider_response_contract"]["status_value"], "queued")
            self.assertEqual(attempt["error_category"], "async_pending")
            self.assertEqual(attempt["routing_action"], "fallback_provider")
            self.assertTrue(attempt["provider_response_contract"]["task_id_present"])
            self.assertNotIn("task-secret-should-not-be-logged", serialized)

    def test_batch_summary_treats_invalid_artifact_as_pool_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "first"},
                ],
            }), encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"
            adapter = FakeAdapter("p1", invalid_artifact_result("p1"))

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": adapter}, {})):
                run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            batch_event = [
                json.loads(line)
                for line in perf_log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ][-1]
            summary = batch_event["pool_outcome_summary"]["p1"]

            self.assertEqual(summary["success_count"], 0)
            self.assertEqual(summary["success_rate"], 0.0)
            self.assertEqual(summary["status"], "pool_risk_no_recent_success")
            self.assertEqual(summary["errors"], {"invalid_artifact": 1})
            self.assertEqual(batch_event["error_category_totals"]["invalid_artifact"], 1)

    def test_batch_summary_records_reference_size_metrics_per_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.png"
            ref.write_bytes(b"12345")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "image_to_image", "prompt": "first", "referenceImages": [str(ref)]},
                ],
            }), encoding="utf-8")
            perf_log_path = root / "logs" / "performance.jsonl"
            adapter = FakeAdapter("p1", result("p1"))

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": adapter}, {})):
                run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log_path),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            batch_event = [
                json.loads(line)
                for line in perf_log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ][-1]
            attempt = batch_event["route_decisions"]["one"]["attempts"][0]
            self.assertEqual(attempt["reference_original_total_bytes"], 5)
            self.assertEqual(attempt["reference_used_total_bytes"], 5)
            self.assertEqual(attempt["reference_used_estimated_base64_bytes"], 8)
            self.assertEqual(attempt["timings_ms"]["provider_http_ms"], 100)


if __name__ == "__main__":
    unittest.main()
