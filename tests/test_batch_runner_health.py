from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from router.core import batch_runner
from router.core import ledger as ledger_module
from router.core.batch_runner import _execute_single_request, run_job_file
from router.core.models import CapabilityBucket, GenerationRequest, GenerationResult, ProviderCapability
from router.core.provider_locks import ProviderLeaseManager
from router.core.scheduler import ProviderState


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def png_header_with_size(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        from datetime import datetime

        return datetime(2026, 6, 3, 7, 19, 23, tzinfo=tz)


class FakeAdapter:
    def __init__(
        self,
        provider: str,
        results: list[GenerationResult] | None = None,
        *,
        max_concurrency: int = 1,
    ):
        self.provider = provider
        self.results = list(results or [])
        self.calls = 0
        self.max_concurrency = max_concurrency

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider,
            bucket=CapabilityBucket.MULTI_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=True,
            max_reference_images=5,
            max_concurrency=self.max_concurrency,
            enabled=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        if self.results:
            return self.results.pop(0)
        return success_result(self.provider)


class DelayedAdapter(FakeAdapter):
    def __init__(
        self,
        provider: str,
        delay_seconds: float,
        start_times: list[tuple[str, float]] | None = None,
        finish_times: list[tuple[str, float]] | None = None,
        timing_lock: threading.Lock | None = None,
        max_concurrency: int = 1,
    ):
        super().__init__(provider, [success_result(provider)], max_concurrency=max_concurrency)
        self.delay_seconds = delay_seconds
        self.completed = threading.Event()
        self.start_times = start_times
        self.finish_times = finish_times
        self.timing_lock = timing_lock

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if self.start_times is not None:
            if self.timing_lock:
                with self.timing_lock:
                    self.start_times.append((self.provider, time.perf_counter()))
            else:
                self.start_times.append((self.provider, time.perf_counter()))
        try:
            time.sleep(self.delay_seconds)
            return super().generate(request)
        finally:
            if self.finish_times is not None:
                if self.timing_lock:
                    with self.timing_lock:
                        self.finish_times.append((self.provider, time.perf_counter()))
                else:
                    self.finish_times.append((self.provider, time.perf_counter()))
            self.completed.set()


class ItemAwareAdapter(FakeAdapter):
    def __init__(
        self,
        provider: str,
        *,
        fail_original_ids: set[str] | None = None,
        slow_original_ids: set[str] | None = None,
        fast_delay_seconds: float = 0.01,
        slow_delay_seconds: float = 0.16,
        max_concurrency: int = 10,
    ):
        super().__init__(provider, max_concurrency=max_concurrency)
        self.fail_original_ids = set(fail_original_ids or set())
        self.slow_original_ids = set(slow_original_ids or set())
        self.fast_delay_seconds = fast_delay_seconds
        self.slow_delay_seconds = slow_delay_seconds
        self.events: list[tuple[str, str, float]] = []
        self.event_lock = threading.Lock()

    def _record(self, event: str, item_id: str) -> None:
        with self.event_lock:
            self.events.append((event, item_id, time.perf_counter()))

    def generate(self, request: GenerationRequest) -> GenerationResult:
        item_id = str(request.metadata.get("batchItemId") or "")
        self.calls += 1
        self._record("start", item_id)
        try:
            if item_id in self.fail_original_ids:
                time.sleep(self.fast_delay_seconds)
                return failure_result(self.provider, "HTTPError", "HTTP Error 503: upstream failed")
            delay = self.slow_delay_seconds if item_id in self.slow_original_ids else self.fast_delay_seconds
            time.sleep(delay)
            return success_result(self.provider)
        finally:
            self._record("finish", item_id)


class BlockingAdapter(FakeAdapter):
    def __init__(
        self,
        provider: str,
        *,
        delay_seconds: float,
        result: GenerationResult | None = None,
        max_concurrency: int = 1,
    ):
        super().__init__(provider, [result or success_result(provider)], max_concurrency=max_concurrency)
        self.delay_seconds = delay_seconds
        self.started = threading.Event()
        self.finished = threading.Event()

    def generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls += 1
        self.started.set()
        try:
            time.sleep(self.delay_seconds)
            if self.results:
                return self.results.pop(0)
            return success_result(self.provider)
        finally:
            self.finished.set()


class PreflightFailAdapter(FakeAdapter):
    def __init__(self, provider: str):
        super().__init__(provider, [success_result(provider)], max_concurrency=1)
        self.health_checks = 0

    def health_check(self) -> GenerationResult:
        self.health_checks += 1
        return failure_result(self.provider, "SSLError", "TLS handshake failed")


def request(**overrides) -> GenerationRequest:
    values = {
        "job_type": "manifest",
        "request_type": "text_to_image",
        "profile": "generic",
        "prompt": "draw a small robot",
        "routing_mode": "fallback",
    }
    values.update(overrides)
    return GenerationRequest(**values)


def success_result(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_blobs=[PNG_1X1],
        provider_response={"ok": True, "provider": provider},
        latency_ms=10,
    )


def success_result_with_size(provider: str, width: int, height: int) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        artifact_blobs=[png_header_with_size(width, height)],
        provider_response={"ok": True, "provider": provider},
        latency_ms=10,
    )


def success_image_to_image_result_with_size(provider: str, width: int, height: int, reference_count: int) -> GenerationResult:
    return GenerationResult(
        ok=True,
        provider=provider,
        model="fake-model",
        request_type="image_to_image",
        reference_count=reference_count,
        artifact_blobs=[png_header_with_size(width, height)],
        provider_response={"ok": True, "provider": provider},
        latency_ms=10,
    )


def failure_result(provider: str, code: str, message: str) -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code=code,
        error_message=message,
        latency_ms=10,
    )


def async_pending_result(provider: str) -> GenerationResult:
    return GenerationResult(
        ok=False,
        provider=provider,
        model="fake-model",
        request_type="text_to_image",
        reference_count=0,
        error_code="no_image",
        error_message="No image data found in provider response.",
        provider_response={
            "status": "processing",
            "task_id": "task-secret-should-not-be-copied",
        },
        latency_ms=10,
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
        latency_ms=10,
    )


class BatchRunnerHealthTests(unittest.TestCase):
    def test_attempt_report_includes_comparison_contract_and_dimensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            refs = []
            for idx in range(5):
                path = Path(tmp) / f"ref-{idx}.png"
                path.write_bytes(PNG_1X1)
                refs.append(str(path))
            adapters = {
                "simage-provider": FakeAdapter(
                    "simage-provider",
                    [success_image_to_image_result_with_size("simage-provider", 2048, 1152, 5)],
                )
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}
            metadata = {
                "pool_identity": "group-a/station-a/simage-provider",
                "provider_group": "group-a",
                "station_id": "station-a",
                "model_class": "Simage",
                "production_line": "simage",
                "model": "gpt-image-2",
                "degradation_status": "active",
                "image_contract": {
                    "route_path": "/images/generations",
                    "requestSizeMode": "passthrough",
                    "defaultSize": "2048x2048",
                    "imageUrlsField": "image_urls",
                },
            }

            with patch.object(batch_runner, "provider_pool_metadata_for", return_value=metadata):
                result = _execute_single_request(
                    request(
                        request_type="image_to_image",
                        reference_images=refs,
                        size="2K",
                        metadata={"artifactDimensionMode": "long_edge", "artifactMinLongEdge": 2048},
                    ),
                    item_id="item-001",
                    adapters=adapters,
                    output_dir=tmp,
                    provider_preference=["simage-provider"],
                    registry=registry,
                    states=states,
                    lock=threading.Lock(),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            attempt = result.attempts[0]
            self.assertEqual(attempt["comparison_contract"]["production_line"], "simage")
            self.assertEqual(attempt["comparison_contract"]["station"], "station-a")
            self.assertEqual(attempt["comparison_contract"]["model"], "gpt-image-2")
            self.assertEqual(attempt["comparison_contract"]["reference_count"], 5)
            self.assertEqual(attempt["comparison_contract"]["degradation_status"], "active")
            self.assertEqual(attempt["artifact_dimensions"], [{"width": 2048, "height": 1152, "long_edge": 2048}])
            self.assertEqual(attempt["latency_ms"], 10)
            self.assertIsInstance(attempt["scheduling_wait_ms"], int)
            self.assertEqual(attempt["failure_type"], "success")

    def test_same_second_batch_outputs_use_unique_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "one", "requestType": "text_to_image", "prompt": "one"}],
            }), encoding="utf-8")

            with (
                patch.object(batch_runner, "datetime", FixedDateTime),
                patch.object(ledger_module, "datetime", FixedDateTime),
                patch.object(batch_runner, "_build_adapters", return_value=({"p1": FakeAdapter("p1")}, {})),
            ):
                first = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )
                second = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertNotEqual(first.summary_path, second.summary_path)
            self.assertNotEqual(first.ledger_path, second.ledger_path)
            self.assertTrue(Path(first.summary_path).exists())
            self.assertTrue(Path(second.summary_path).exists())
            self.assertTrue(Path(first.ledger_path).exists())
            self.assertTrue(Path(second.ledger_path).exists())

    def test_retry_before_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [
                    failure_result("p1", "TimeoutError", "timed out"),
                    success_result("p1"),
                ]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=1,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p1")
            self.assertEqual(result.attempted_providers, ["p1"])
            self.assertEqual(len(result.attempts), 2)
            self.assertEqual(result.attempts[0]["error_category"], "timeout")
            self.assertEqual(result.attempts[0]["routing_action"], "retry_same_provider")

    def test_async_pending_response_falls_back_without_duplicate_same_provider_submit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [
                    async_pending_result("p1"),
                    success_result("p1"),
                ]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=1,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(adapters["p1"].calls, 1)
            self.assertEqual(result.attempts[0]["error_category"], "async_pending")
            self.assertEqual(result.attempts[0]["routing_action"], "fallback_provider")
            self.assertTrue(result.attempts[0]["provider_response_contract"]["async_pending"])
            self.assertTrue(result.attempts[0]["provider_response_contract"]["task_id_present"])

    def test_invalid_artifact_falls_back_to_next_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [invalid_artifact_result("p1")]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(result.attempted_providers, ["p1", "p2"])
            self.assertFalse(result.attempts[0]["ok"])
            self.assertEqual(result.attempts[0]["error_category"], "invalid_artifact")
            self.assertEqual(result.attempts[0]["routing_action"], "fallback_provider")
            self.assertEqual(result.attempts[1]["provider"], "p2")

    def test_strict_min_artifact_dimensions_falls_back_to_next_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [success_result("p1")]),
                "p2": FakeAdapter("p2", [success_result_with_size("p2", 4096, 4096)]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(metadata={"artifactMinWidth": 4096, "artifactMinHeight": 4096}),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(result.attempted_providers, ["p1", "p2"])
            self.assertFalse(result.attempts[0]["ok"])
            self.assertEqual(result.attempts[0]["error_category"], "invalid_artifact")
            self.assertEqual(result.attempts[0]["artifact_metrics"]["dimension_mismatch_count"], 1)
            self.assertEqual(result.attempts[0]["artifact_metrics"]["dimension_contract_mode"], "long_edge")
            self.assertIn("long edge >= 4096", result.attempts[0]["error_message"])

    def test_invalid_artifact_single_provider_failure_exposes_effective_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {"p1": FakeAdapter("p1", [invalid_artifact_result("p1")])}
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.selected_provider, "p1")
            self.assertEqual(result.error_code, "invalid_artifact")
            self.assertIn("invalid_artifact", result.error_message or "")

    def test_fallback_skips_provider_in_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [success_result("p1")]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}
            states["p1"].cooldown_until = time.time() + 60
            states["p1"].cooldown_reason = "rate_limit_or_quota"
            states["p1"].last_error_category = "rate_limit"

            result = _execute_single_request(
                request(),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(result.skipped_providers[0]["provider"], "p1")
            self.assertEqual(result.skipped_providers[0]["reason"], "cooldown")

    def test_repeated_retryable_failures_trip_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                    {"id": "three", "requestType": "text_to_image", "prompt": "three"},
                    {"id": "four", "requestType": "text_to_image", "prompt": "four"},
                ],
            }), encoding="utf-8")

            adapters = {
                "p1": FakeAdapter("p1", [
                    failure_result("p1", "TimeoutError", "The request timed out"),
                    failure_result("p1", "TimeoutError", "The request timed out"),
                    failure_result("p1", "TimeoutError", "The request timed out"),
                ]),
                "p2": FakeAdapter("p2", [
                    success_result("p2"),
                    success_result("p2"),
                    success_result("p2"),
                    success_result("p2"),
                ]),
            }
            state_path = root / "provider-state.json"

            with patch.dict(os.environ, {
                "MIR_CIRCUIT_BREAKER_FAILURE_THRESHOLD": "3",
                "MIR_CIRCUIT_BREAKER_COOLDOWN_SECONDS": "120",
            }, clear=False):
                with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                    result = run_job_file(
                        str(job),
                        providers=["p1", "p2"],
                        output_dir=str(root / "artifacts"),
                        batch_dir=str(root / "batches"),
                        ledger_dir=str(root / "ledger"),
                        provider_state_path=str(state_path),
                        max_workers=1,
                        max_retries_per_provider=0,
                        retry_delay_seconds=0,
                    )

            self.assertEqual(result.success_count, 4)
            self.assertEqual(result.item_results[2].attempts[0]["routing_action"], "cooldown_provider")
            self.assertEqual(result.item_results[2].attempts[0]["cooldown_seconds"], 120.0)
            self.assertEqual(result.item_results[3].skipped_providers[0]["provider"], "p1")
            self.assertEqual(result.item_results[3].skipped_providers[0]["reason"], "cooldown")

            state = json.loads(state_path.read_text(encoding="utf-8"))["providers"]["p1"]
            self.assertEqual(state["cooldown_reason"], "circuit_breaker_timeout")
            self.assertEqual(state["consecutive_failures"], 3)

    def test_health_aware_preflight_connection_failure_falls_back_without_generation_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": PreflightFailAdapter("p1"),
                "p2": FakeAdapter("p2", [success_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(metadata={"routingPolicy": "health_aware_load_balance"}),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(adapters["p1"].health_checks, 1)
            self.assertEqual(adapters["p1"].calls, 0)
            self.assertEqual(result.attempts[0]["provider"], "p1")
            self.assertTrue(result.attempts[0]["preflight_check"])
            self.assertEqual(result.attempts[0]["error_category"], "connection")
            self.assertEqual(result.attempts[0]["routing_action"], "cooldown_provider")
            self.assertGreater(result.attempts[0]["cooldown_seconds"], 0)
            self.assertEqual(result.attempts[1]["provider"], "p2")

    def test_summary_records_local_scheduling_wait_when_lane_concurrency_is_full(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                ],
            }), encoding="utf-8")

            adapters = {"p1": DelayedAdapter("p1", delay_seconds=0.08)}

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=2,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 2)
            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            waits = [
                item["attempts"][0]["scheduling_wait_ms"]
                for item in summary["items"]
            ]
            self.assertGreaterEqual(max(waits), 40)
            self.assertEqual(summary["provider_queue_wait_summary"]["p1"]["count"], 2)
            self.assertGreaterEqual(summary["provider_queue_wait_summary"]["p1"]["max_ms"], 40)
            self.assertEqual(summary["pool_queue_wait_summary"]["p1"]["count"], 2)

    def test_cross_process_provider_lock_delays_batch_attempt_until_slot_is_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                ],
            }), encoding="utf-8")
            lock_dir = root / "provider-locks"
            manager = ProviderLeaseManager(lock_dir, lease_timeout_seconds=60)
            lease = manager.acquire("p1", max_concurrency=1, task_id="external")
            self.assertIsNotNone(lease)

            def release_later() -> None:
                time.sleep(0.08)
                manager.release(lease)

            thread = threading.Thread(target=release_later)
            thread.start()
            adapters = {"p1": FakeAdapter("p1", [success_result("p1")])}

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    provider_lock_dir=str(lock_dir),
                    cross_process_provider_locks=True,
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )
            thread.join(timeout=1)

            self.assertEqual(result.success_count, 1)
            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            wait_ms = summary["items"][0]["attempts"][0]["scheduling_wait_ms"]
            self.assertGreaterEqual(wait_ms, 40)
            self.assertEqual(list((lock_dir / "p1").glob("lease-*.json")), [])

    def test_race_dedupes_provider_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "vectorengine": FakeAdapter("vectorengine", [success_result("vectorengine")]),
                "vectorengine_compat": FakeAdapter("vectorengine_compat", [success_result("vectorengine_compat")]),
                "gptge": FakeAdapter("gptge", [success_result("gptge")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(routing_mode="race", race_providers=["vectorengine", "vectorengine_compat", "gptge"]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["vectorengine", "vectorengine_compat", "gptge"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.race_launched_providers, ["vectorengine", "gptge"])
            self.assertNotIn("vectorengine_compat", result.race_launched_providers)

    def test_race_uses_second_same_group_provider_when_first_is_cooled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "vectorengine": FakeAdapter("vectorengine", [success_result("vectorengine")]),
                "vectorengine_compat": FakeAdapter("vectorengine_compat", [success_result("vectorengine_compat")]),
                "gptge": FakeAdapter("gptge", [success_result("gptge")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}
            states["vectorengine"].cooldown_until = time.time() + 60
            states["vectorengine"].cooldown_reason = "rate_limit_or_quota"

            result = _execute_single_request(
                request(routing_mode="race", race_providers=["vectorengine", "vectorengine_compat", "gptge"]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["vectorengine", "vectorengine_compat", "gptge"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.race_launched_providers, ["vectorengine_compat", "gptge"])
            self.assertEqual(result.skipped_providers[0]["provider"], "vectorengine")

    def test_race_waits_for_launched_provider_threads_before_returning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "vectorengine": FakeAdapter("vectorengine", [success_result("vectorengine")]),
                "gptge": DelayedAdapter("gptge", delay_seconds=0.02),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(routing_mode="race", race_providers=["vectorengine", "gptge"]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["vectorengine", "gptge"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertTrue(adapters["gptge"].completed.is_set())

    def test_race_ignores_invalid_artifact_and_waits_for_valid_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [invalid_artifact_result("p1")]),
                "p2": DelayedAdapter("p2", delay_seconds=0.02),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(routing_mode="race", race_providers=["p1", "p2"]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(result.race_launched_providers, ["p1", "p2"])
            self.assertEqual(result.attempts[0]["error_category"], "invalid_artifact")
            self.assertFalse(result.attempts[0]["ok"])
            self.assertTrue(adapters["p2"].completed.is_set())

    def test_race_all_invalid_artifacts_exposes_effective_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": FakeAdapter("p1", [invalid_artifact_result("p1")]),
                "p2": FakeAdapter("p2", [invalid_artifact_result("p2")]),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            result = _execute_single_request(
                request(routing_mode="race", race_providers=["p1", "p2"]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["p1", "p2"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "invalid_artifact")
            self.assertIn("invalid_artifact", result.error_message or "")
            self.assertTrue(all(not attempt["ok"] for attempt in result.attempts))

    def test_summary_contains_error_category_and_skipped_providers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "first"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "second"},
                ],
            }), encoding="utf-8")

            p1 = FakeAdapter("p1", [
                failure_result("p1", "HTTPError", "HTTP Error 429: Too Many Requests"),
                failure_result("p1", "HTTPError", "HTTP Error 429: Too Many Requests"),
            ])
            p2 = FakeAdapter("p2", [success_result("p2"), success_result("p2")])

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": p1, "p2": p2}, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1", "p2"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            ledger = json.loads(Path(result.ledger_path).read_text(encoding="utf-8"))

            self.assertEqual(ledger["schema_version"], "batch-ledger/v1")
            self.assertEqual(summary["items"][0]["attempts"][0]["error_category"], "rate_limit")
            self.assertEqual(summary["items"][0]["attempts"][0]["routing_action"], "cooldown_provider")
            self.assertEqual(summary["items"][1]["skipped_providers"][0]["provider"], "p1")
            self.assertIn("artifact_paths", summary["items"][1])
            self.assertEqual(summary["provider_attempt_totals"], {"p1": 1, "p2": 2})
            self.assertEqual(summary["pool_attempt_totals"], {"p1": 1, "p2": 2})
            self.assertEqual(summary["pool_error_category_totals"]["p1"]["rate_limit"], 1)
            self.assertEqual(summary["pool_error_category_totals"]["p2"]["success"], 2)
            self.assertEqual(summary["pool_outcome_summary"]["p1"]["status"], "pool_risk_no_recent_success")
            self.assertEqual(summary["pool_outcome_summary"]["p2"]["success_count"], 2)
            self.assertEqual(summary["pool_outcome_summary"]["p2"]["success_rate"], 1.0)
            self.assertEqual(
                summary["pool_outcome_status_counts"],
                {"pool_risk_no_recent_success": 1, "ready_recent_success": 1},
            )

    def test_mixed_batch_without_real_api_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.png"
            ref.write_bytes(b"fake-ref")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "text", "requestType": "text_to_image", "prompt": "text"},
                    {"id": "single", "requestType": "image_to_image", "prompt": "single", "referenceImages": [str(ref)]},
                    {"id": "multi", "requestType": "image_to_image", "prompt": "multi", "referenceImages": [str(ref), str(ref)]},
                ],
            }), encoding="utf-8")

            fake = FakeAdapter("p1", [success_result("p1"), success_result("p1"), success_result("p1")])

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": fake}, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 3)
            self.assertEqual(fake.calls, 3)

    def test_load_balance_policy_spreads_item_starting_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "load_balance",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                    {"id": "three", "requestType": "text_to_image", "prompt": "three"},
                ],
            }), encoding="utf-8")

            adapters = {
                "p1": FakeAdapter("p1", [success_result("p1")]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
                "p3": FakeAdapter("p3", [success_result("p3")]),
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1", "p2", "p3"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 3)
            self.assertEqual([item.selected_provider for item in result.item_results], ["p1", "p2", "p3"])
            self.assertEqual([item.planned_providers[0] for item in result.item_results], ["p1", "p2", "p3"])

    def test_load_balance_rotates_starting_provider_inside_item_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            providers = ["p1", "p2", "p3"]
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "load_balance",
                "items": [
                    {"id": f"item-{idx:03d}", "requestType": "text_to_image", "prompt": f"item {idx}"}
                    for idx in range(1, 7)
                ],
            }), encoding="utf-8")

            timing_lock = threading.Lock()
            start_times: list[tuple[str, float]] = []
            finish_times: list[tuple[str, float]] = []
            adapters = {
                provider: DelayedAdapter(
                    provider,
                    delay_seconds=0.08,
                    start_times=start_times,
                    finish_times=finish_times,
                    timing_lock=timing_lock,
                    max_concurrency=2,
                )
                for provider in providers
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=None,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            expected_starts = ["p1", "p2", "p3", "p1", "p2", "p3"]

            self.assertEqual(summary["max_workers"], 6)
            self.assertEqual(result.success_count, 6)
            self.assertEqual([item.planned_providers[0] for item in result.item_results], expected_starts)
            self.assertEqual([item.selected_provider for item in result.item_results], expected_starts)
            first_finish = min(timestamp for _, timestamp in finish_times)
            self.assertCountEqual(
                [provider for provider, timestamp in start_times if timestamp < first_finish],
                expected_starts,
            )

    def test_health_aware_load_balance_cools_connection_failure_within_batch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "health_aware_load_balance",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                ],
            }), encoding="utf-8")

            adapters = {
                "p1": FakeAdapter("p1", [failure_result("p1", "SSLError", "[SSL: RECORD_LAYER_FAILURE] record layer failure")]),
                "p2": FakeAdapter("p2", [success_result("p2"), success_result("p2")]),
                "p3": FakeAdapter("p3", [success_result("p3")]),
            }

            with (
                patch.dict(os.environ, {"MIR_HEALTH_AWARE_FAILURE_COOLDOWN_SECONDS": "120"}, clear=False),
                patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})),
            ):
                result = run_job_file(
                    str(job),
                    providers=["p1", "p2", "p3"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 2)
            self.assertEqual(result.item_results[0].attempted_providers, ["p1", "p2"])
            self.assertEqual(result.item_results[0].attempts[0]["error_category"], "connection")
            self.assertEqual(result.item_results[0].attempts[0]["routing_action"], "cooldown_provider")
            self.assertEqual(result.item_results[1].selected_provider, "p2")
            self.assertEqual(adapters["p1"].calls, 1)
            self.assertEqual(result.item_results[1].skipped_providers[0]["provider"], "p1")
            self.assertEqual(result.item_results[1].skipped_providers[0]["reason"], "cooldown")

    def test_health_aware_load_balance_rotates_starting_provider_like_load_balance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            providers = ["p1", "p2", "p3"]
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "health_aware_load_balance",
                "items": [
                    {"id": f"item-{idx:03d}", "requestType": "text_to_image", "prompt": f"item {idx}"}
                    for idx in range(1, 7)
                ],
            }), encoding="utf-8")

            adapters = {
                provider: FakeAdapter(provider, [success_result(provider), success_result(provider)])
                for provider in providers
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            expected_starts = ["p1", "p2", "p3", "p1", "p2", "p3"]
            self.assertEqual(result.success_count, 6)
            self.assertEqual([item.planned_providers[0] for item in result.item_results], expected_starts)
            self.assertEqual([item.selected_provider for item in result.item_results], expected_starts)

    def test_health_aware_load_balance_launches_backup_after_threshold_without_full_race(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapters = {
                "p1": BlockingAdapter("p1", delay_seconds=0.16, result=success_result("p1")),
                "p2": BlockingAdapter("p2", delay_seconds=0.01, result=success_result("p2")),
                "p3": BlockingAdapter("p3", delay_seconds=0.01, result=success_result("p3")),
            }
            registry = {name: adapter.capability() for name, adapter in adapters.items()}
            states = {name: ProviderState() for name in adapters}

            started = time.perf_counter()
            with patch.dict(os.environ, {"MIR_HEALTH_AWARE_BACKUP_AFTER_SECONDS": "0.03"}, clear=False):
                result = _execute_single_request(
                    request(metadata={"routingPolicy": "health_aware_load_balance"}),
                    item_id="item-001",
                    adapters=adapters,
                    output_dir=tmp,
                    provider_preference=["p1", "p2", "p3"],
                    registry=registry,
                    states=states,
                    lock=threading.Lock(),
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )
            elapsed = time.perf_counter() - started

            self.assertEqual(result.status, "success")
            self.assertEqual(result.selected_provider, "p2")
            self.assertEqual(result.effective_routing_mode, None)
            self.assertEqual(result.race_launched_providers, [])
            self.assertEqual([attempt["provider"] for attempt in result.attempts], ["p2"])
            self.assertEqual(result.attempts[0]["routing_action"], "backup_provider")
            self.assertGreaterEqual(result.attempts[0]["backup_launched_after_ms"], 25)
            self.assertLess(elapsed, 0.12)
            self.assertEqual(adapters["p1"].calls, 1)
            self.assertEqual(adapters["p2"].calls, 1)
            self.assertEqual(adapters["p3"].calls, 0)
            deadline = time.time() + 1
            while states["p1"].inflight and time.time() < deadline:
                time.sleep(0.01)
            self.assertEqual(states["p1"].inflight, 0)

    def test_batch_defaults_to_item_level_concurrency_not_provider_group_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            providers = [f"p{i}" for i in range(1, 7)]
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "load_balance",
                "items": [
                    {"id": f"item-{i:03d}", "requestType": "text_to_image", "prompt": f"item {i}"}
                    for i in range(1, 7)
                ],
            }), encoding="utf-8")

            timing_lock = threading.Lock()
            start_times: list[tuple[str, float]] = []
            finish_times: list[tuple[str, float]] = []
            adapters = {
                provider: DelayedAdapter(
                    provider,
                    delay_seconds=0.08,
                    start_times=start_times,
                    finish_times=finish_times,
                    timing_lock=timing_lock,
                )
                for provider in providers
            }

            started = time.perf_counter()
            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=providers,
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=None,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )
            elapsed = time.perf_counter() - started
            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))

            self.assertEqual(summary["max_workers"], 6)
            self.assertEqual(result.success_count, 6)
            self.assertEqual([item.selected_provider for item in result.item_results], providers)
            self.assertEqual(len(start_times), 6)
            self.assertEqual(len(finish_times), 6)
            first_finish = min(timestamp for _, timestamp in finish_times)
            self.assertEqual(
                [provider for provider, timestamp in start_times if timestamp < first_finish],
                providers,
            )
            self.assertLess(elapsed, 0.18)

    def test_fallback_load_balance_and_race_have_different_execution_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            providers = ["p1", "p2", "p3"]

            def write_job(policy: str) -> Path:
                job = root / f"{policy}.json"
                job.write_text(json.dumps({
                    "jobType": "manifest",
                    "routingPolicy": policy,
                    "items": [
                        {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                        {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                        {"id": "three", "requestType": "text_to_image", "prompt": "three"},
                    ],
                }), encoding="utf-8")
                return job

            def run_policy(policy: str, *, max_workers: int):
                adapters = {
                    name: FakeAdapter(name, [success_result(name), success_result(name), success_result(name)])
                    for name in providers
                }
                with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                    result = run_job_file(
                        str(write_job(policy)),
                        providers=providers,
                        output_dir=str(root / policy / "artifacts"),
                        batch_dir=str(root / policy / "batches"),
                        ledger_dir=str(root / policy / "ledger"),
                        max_workers=max_workers,
                        max_retries_per_provider=0,
                        retry_delay_seconds=0,
                    )
                return result, adapters

            fallback_probe_adapters = {
                "p1": FakeAdapter("p1", [failure_result("p1", "HTTPError", "HTTP Error 429: Too Many Requests")]),
                "p2": FakeAdapter("p2", [success_result("p2")]),
                "p3": FakeAdapter("p3", [success_result("p3")]),
            }
            with patch.object(batch_runner, "_build_adapters", return_value=(fallback_probe_adapters, {})):
                fallback_probe_result = run_job_file(
                    str(write_job("fallback-probe")),
                    providers=providers,
                    output_dir=str(root / "fallback-probe" / "artifacts"),
                    batch_dir=str(root / "fallback-probe" / "batches"),
                    ledger_dir=str(root / "fallback-probe" / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            fallback_result, fallback_adapters = run_policy("fallback", max_workers=1)
            load_balance_result, load_balance_adapters = run_policy("load_balance", max_workers=1)
            race_result, race_adapters = run_policy("speed_first", max_workers=3)

            fallback_probe = fallback_probe_result.item_results[0]
            self.assertEqual(fallback_probe.attempted_providers, ["p1", "p2"])
            self.assertEqual([attempt["provider"] for attempt in fallback_probe.attempts], ["p1", "p2"])
            self.assertEqual(fallback_probe.attempts[0]["routing_action"], "cooldown_provider")
            self.assertGreater(fallback_probe.attempts[0]["cooldown_seconds"], 0)
            self.assertEqual(fallback_probe.attempts[1]["routing_action"], "use_result")
            self.assertEqual(fallback_probe.selected_provider, "p2")

            self.assertEqual([item.selected_provider for item in fallback_result.item_results], ["p1", "p1", "p1"])
            self.assertEqual([item.selected_provider for item in load_balance_result.item_results], ["p1", "p2", "p3"])
            self.assertEqual([item.effective_routing_mode for item in race_result.item_results], ["race", "race", "race"])
            self.assertTrue(all(len(item.race_launched_providers) == 3 for item in race_result.item_results))
            self.assertTrue(all(item.race_requested_providers == providers for item in race_result.item_results))
            self.assertTrue(all(item.selected_provider in providers for item in race_result.item_results))
            self.assertTrue(all("routing_action" in item.attempts[0] for item in race_result.item_results))
            self.assertTrue(all("artifact_metrics" in item.attempts[0] for item in race_result.item_results))
            self.assertEqual(
                {name: adapter.calls for name, adapter in fallback_adapters.items()},
                {"p1": 3, "p2": 0, "p3": 0},
            )
            self.assertEqual(
                {name: adapter.calls for name, adapter in load_balance_adapters.items()},
                {"p1": 1, "p2": 1, "p3": 1},
            )
            self.assertEqual(
                {name: adapter.calls for name, adapter in race_adapters.items()},
                {"p1": 3, "p2": 3, "p3": 3},
            )

    def test_weighted_load_balance_uses_perf_log_without_starving_unknown_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_log = root / "performance.jsonl"
            perf_log.write_text("\n".join([
                json.dumps({"event": "generation_result", "provider": "fast", "ok": True, "latency_ms": 12000}),
                json.dumps({"event": "generation_result", "provider": "fast", "ok": True, "latency_ms": 14000}),
                json.dumps({"event": "generation_result", "provider": "slow", "ok": True, "latency_ms": 70000}),
                json.dumps({"event": "generation_result", "provider": "broken", "ok": False, "error_message": "HTTP Error 401: Unauthorized"}),
            ]) + "\n", encoding="utf-8")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "weighted_load_balance",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                    {"id": "three", "requestType": "text_to_image", "prompt": "three"},
                    {"id": "four", "requestType": "text_to_image", "prompt": "four"},
                    {"id": "five", "requestType": "text_to_image", "prompt": "five"},
                    {"id": "six", "requestType": "text_to_image", "prompt": "six"},
                    {"id": "seven", "requestType": "text_to_image", "prompt": "seven"},
                ],
            }), encoding="utf-8")

            adapters = {
                "fast": FakeAdapter("fast", [success_result("fast"), success_result("fast"), success_result("fast")]),
                "slow": FakeAdapter("slow", [success_result("slow")]),
                "unknown": FakeAdapter("unknown", [success_result("unknown")]),
                "broken": FakeAdapter("broken", [success_result("broken")]),
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    providers=["fast", "slow", "unknown", "broken"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    perf_log_path=str(perf_log),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 7)
            self.assertEqual(
                [item.selected_provider for item in result.item_results],
                ["fast", "fast", "fast", "slow", "slow", "unknown", "broken"],
            )
            self.assertEqual(result.item_results[0].routing_policy, "weighted_load_balance")

    def test_weighted_load_balance_uses_recent_window_over_stale_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_log = root / "performance.jsonl"
            perf_log.write_text("\n".join([
                json.dumps({
                    "event": "generation_result",
                    "provider": "stale-fast",
                    "ok": True,
                    "latency_ms": 1000,
                    "recorded_at": 0,
                }),
                json.dumps({
                    "event": "generation_result",
                    "provider": "recent-fast",
                    "ok": True,
                    "latency_ms": 12000,
                }),
            ]) + "\n", encoding="utf-8")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "weighted_load_balance",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                ],
            }), encoding="utf-8")

            adapters = {
                "stale-fast": FakeAdapter("stale-fast", [success_result("stale-fast")]),
                "recent-fast": FakeAdapter("recent-fast", [success_result("recent-fast")]),
            }

            with patch.dict(os.environ, {"MIR_WEIGHTED_PERF_WINDOW_SECONDS": "60"}, clear=False):
                with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                    result = run_job_file(
                        str(job),
                        providers=["stale-fast", "recent-fast"],
                        output_dir=str(root / "artifacts"),
                        batch_dir=str(root / "batches"),
                        ledger_dir=str(root / "ledger"),
                        perf_log_path=str(perf_log),
                        max_workers=1,
                        max_retries_per_provider=0,
                        retry_delay_seconds=0,
                    )

            self.assertEqual(result.item_results[0].selected_provider, "recent-fast")

    def test_batch_replenishes_failed_items_before_waiting_for_all_original_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "metadata": {
                    "targetSuccessCount": 10,
                    "maxReplacementItems": 10,
                    "replacementSource": "same_request_template",
                },
                "items": [
                    {"id": f"item-{idx:03d}", "requestType": "text_to_image", "prompt": f"item {idx}"}
                    for idx in range(1, 11)
                ],
            }), encoding="utf-8")

            adapter = ItemAwareAdapter(
                "p1",
                fail_original_ids={"item-001", "item-002"},
                slow_original_ids={f"item-{idx:03d}" for idx in range(3, 11)},
            )

            with patch.object(batch_runner, "_build_adapters", return_value=({"p1": adapter}, {})):
                result = run_job_file(
                    str(job),
                    providers=["p1"],
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=10,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            starts = {item_id: timestamp for event, item_id, timestamp in adapter.events if event == "start"}
            original_slow_finishes = [
                timestamp
                for event, item_id, timestamp in adapter.events
                if event == "finish" and item_id.startswith("item-") and item_id not in {"item-001", "item-002"}
            ]
            replacement_ids = [item.item_id for item in result.item_results if "-repl-" in item.item_id]

            self.assertEqual(result.success_count, 10)
            self.assertEqual(summary["target_success_count"], 10)
            self.assertEqual(summary["replacement_item_count"], 2)
            self.assertEqual(len(replacement_ids), 2)
            self.assertTrue(all(starts[item_id] < min(original_slow_finishes) for item_id in replacement_ids))
            for item_id in replacement_ids:
                row = next(item for item in summary["items"] if item["item_id"] == item_id)
                self.assertIn(row["replacement_for_item_id"], {"item-001", "item-002"})
                self.assertEqual(row["replacement_reason"], "terminal_item_failure")
                self.assertIsNotNone(row["replacement_created_at"])
                self.assertIsNotNone(row["replacement_started_at"])
                self.assertEqual(row["replacement_provider_plan"], ["p1"])

    def test_default_routing_prefers_professional_tier_when_no_provider_is_named(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                ],
            }), encoding="utf-8")

            adapters = {
                "aiwave": FakeAdapter("aiwave", [success_result("aiwave")]),
                "gptge": FakeAdapter("gptge", [success_result("gptge")]),
                "laozhang": FakeAdapter("laozhang", [success_result("laozhang"), success_result("laozhang")]),
                "apiyi": FakeAdapter("apiyi", [success_result("apiyi")]),
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 2)
            self.assertEqual([item.selected_provider for item in result.item_results], ["laozhang", "laozhang"])
            self.assertTrue(all(item.planned_providers[0] == "laozhang" for item in result.item_results))

    def test_daily_tier_load_balance_spreads_across_daily_websites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "routingPolicy": "load_balance",
                "providerTier": "普通组",
                "items": [
                    {"id": "one", "requestType": "text_to_image", "prompt": "one"},
                    {"id": "two", "requestType": "text_to_image", "prompt": "two"},
                    {"id": "three", "requestType": "text_to_image", "prompt": "three"},
                    {"id": "four", "requestType": "text_to_image", "prompt": "four"},
                ],
            }), encoding="utf-8")

            adapters = {
                "aiwave": FakeAdapter("aiwave", [success_result("aiwave")]),
                "gptge": FakeAdapter("gptge", [success_result("gptge")]),
                "aifast": FakeAdapter("aifast", [success_result("aifast")]),
                "vectorengine_compat": FakeAdapter("vectorengine_compat", [success_result("vectorengine_compat")]),
                "laozhang": FakeAdapter("laozhang", [success_result("laozhang")]),
                "apiyi": FakeAdapter("apiyi", [success_result("apiyi")]),
            }

            with patch.object(batch_runner, "_build_adapters", return_value=(adapters, {})):
                result = run_job_file(
                    str(job),
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    max_workers=1,
                    max_retries_per_provider=0,
                    retry_delay_seconds=0,
                )

            self.assertEqual(result.success_count, 4)
            self.assertEqual(
                [item.selected_provider for item in result.item_results],
                ["aiwave", "gptge", "aifast", "vectorengine_compat"],
            )

    def test_dry_run_can_plan_configured_lanes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_config = root / "sources.yaml"
            source_config.write_text("""
schemaVersion: multi-source-config/v1
stations:
  laozhang-main:
    kind: relay
    baseUrl: https://api.laozhang.ai
    vendorGroup: laozhang-group
    enabled: true
accounts:
  laozhang-primary:
    station: laozhang-main
    accountGroup: pro-paid-pool
    enabled: true
credentials:
  laozhang-key3:
    account: laozhang-primary
    secretRef:
      type: env
      name: MIR_LAOZHANG_KEY3_API_KEY
    authScheme: bearer
    enabled: true
routes:
  laozhang-openai-images:
    station: laozhang-main
    adapterFamily: openai_images
    path: /v1/images/generations
    enabled: true
lanes:
  laozhang-key3-openai-images:
    provider: laozhang
    providerGroup: laozhang-group
    sourceTier: professional
    station: laozhang-main
    account: laozhang-primary
    credential: laozhang-key3
    route: laozhang-openai-images
    poolIdentity: laozhang-group/laozhang-main/key3-openai-images
    model: gemini-3-pro-image-preview
    capabilityBucket: T
    maxReferenceImages: 0
    maxConcurrency: 1
    priority: 10
    enabled: true
""", encoding="utf-8")
            job = root / "job.json"
            job.write_text(json.dumps({
                "jobType": "manifest",
                "items": [{"id": "text", "requestType": "text_to_image", "prompt": "text"}],
            }), encoding="utf-8")

            with patch.dict(os.environ, {
                "MIR_SOURCES_CONFIG_FILE": str(source_config),
                "MIR_LAOZHANG_KEY3_API_KEY": "test-secret",
            }, clear=False):
                result = run_job_file(
                    str(job),
                    output_dir=str(root / "artifacts"),
                    batch_dir=str(root / "batches"),
                    ledger_dir=str(root / "ledger"),
                    dry_run=True,
                )

            summary = json.loads(Path(result.summary_path).read_text(encoding="utf-8"))
            self.assertEqual(result.success_count, 1)
            self.assertEqual(summary["items"][0]["planned_providers"][0], "laozhang-key3-openai-images")
            self.assertEqual(summary["items"][0]["capability_decisions"][0]["provider_group"], "laozhang-group")
            self.assertEqual(
                summary["items"][0]["capability_decisions"][0]["pool_identity"],
                "laozhang-group/laozhang-main/key3-openai-images",
            )
            self.assertEqual(summary["items"][0]["capability_decisions"][0]["account_group"], "pro-paid-pool")
            self.assertEqual(summary["items"][0]["capability_decisions"][0]["credential_id"], "laozhang-key3")


if __name__ == "__main__":
    unittest.main()
