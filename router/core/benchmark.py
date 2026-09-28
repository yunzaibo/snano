from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from router.core.batch_runner import run_job_file
from router.core.provider_registry import ordered_lane_ids, provider_startup_diagnostics
from router.core.routing_presets import RoutingPreset


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BENCHMARK_PROMPT = (
    "A simple product photo of a small white ceramic cup on a plain table, "
    "realistic lighting, one image only."
)


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    providers: list[str] | None = None
    preset: RoutingPreset | None = None
    samples: int = 1
    prompt: str = DEFAULT_BENCHMARK_PROMPT
    profile: str = "generic"
    output_dir: str = str(PROJECT_ROOT / "data" / "artifacts" / "internal-benchmark")
    batch_dir: str = str(PROJECT_ROOT / "data" / "batches" / "internal-benchmark")
    ledger_dir: str = str(PROJECT_ROOT / "data" / "ledger" / "internal-benchmark")
    perf_log_file: str | None = None
    provider_state_file: str | None = None


def benchmark_perf_log_path(now: datetime | None = None) -> str:
    current = now or datetime.now(timezone.utc)
    stamp = current.strftime("%Y%m%dT%H%M%SZ")
    return str(PROJECT_ROOT / "logs" / f"internal-benchmark-{stamp}.jsonl")


def _eligible_configured_providers() -> list[str]:
    rows = provider_startup_diagnostics()
    providers = [
        str(row.get("provider"))
        for row in rows
        if row.get("provider") and bool(row.get("enabled", True)) and bool(row.get("key_present"))
    ]
    if providers:
        return providers
    return ordered_lane_ids()


def resolve_benchmark_providers(config: BenchmarkConfig) -> list[str]:
    if config.providers:
        return [name for name in config.providers if name]
    if config.preset and config.preset.providers:
        return list(config.preset.providers)
    return _eligible_configured_providers()


def _build_job(providers: list[str], *, samples: int, prompt: str, profile: str) -> dict[str, Any]:
    clamped_samples = min(max(samples, 1), 3)
    items: list[dict[str, Any]] = []
    for provider in providers:
        for index in range(1, clamped_samples + 1):
            items.append({
                "id": f"{provider}-sample-{index}",
                "requestType": "text_to_image",
                "prompt": prompt,
                "profile": profile,
                "quality": "standard",
                "size": "1024x1024",
                "providers": [provider],
                "metadata": {
                    "source": "internal-benchmark",
                    "benchmarkProvider": provider,
                    "benchmarkSample": index,
                },
            })
    return {
        "jobType": "manifest",
        "profile": profile,
        "routingPolicy": "fallback",
        "items": items,
    }


def run_internal_benchmark(config: BenchmarkConfig) -> dict[str, Any]:
    providers = resolve_benchmark_providers(config)
    if not providers:
        raise ValueError("No benchmark providers resolved. Pass --provider or configure internal sources with keys.")

    perf_log_file = config.perf_log_file or benchmark_perf_log_path()
    with TemporaryDirectory(prefix="mir-benchmark-") as tmp:
        job_path = Path(tmp) / "benchmark-job.json"
        job_path.write_text(
            json.dumps(
                _build_job(providers, samples=config.samples, prompt=config.prompt, profile=config.profile),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result = run_job_file(
            str(job_path),
            providers=providers,
            output_dir=config.output_dir,
            batch_dir=config.batch_dir,
            ledger_dir=config.ledger_dir,
            perf_log_path=perf_log_file,
            provider_state_path=config.provider_state_file,
            default_profile=config.profile,
            max_workers=1,
            max_retries_per_provider=0,
            retry_delay_seconds=0,
            routing_policy="fallback",
            provider_tier=config.preset.provider_tier if config.preset else None,
            preset_name=config.preset.name if config.preset else None,
            routing_preset=config.preset,
            dry_run=False,
        )

    return {
        "ok": result.failure_count == 0,
        "benchmark": True,
        "providers": providers,
        "samples_per_provider": min(max(config.samples, 1), 3),
        "perf_log_path": perf_log_file,
        "summary_path": result.summary_path,
        "ledger_path": result.ledger_path,
        "success_count": result.success_count,
        "failure_count": result.failure_count,
        "items": [
            {
                "item_id": item.item_id,
                "status": item.status,
                "selected_provider": item.selected_provider,
                "latency_ms": item.latency_ms,
                "error_code": item.error_code,
                "error_message": item.error_message,
                "artifact_paths": item.artifact_paths,
            }
            for item in result.item_results
        ],
    }
