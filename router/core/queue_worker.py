from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from router.core.artifacts import write_result
from router.core.batch_runner import BatchItemResult, load_job_items_with_defaults, run_job_file
from router.core.dedupe import request_hash
from router.core.ledger import write_batch_ledger
from router.core.perf_log import append_batch_summary
from router.core.provider_registry import build_adapter
from router.core.queue_scheduler import QueueScheduler
from router.core.queue_store import QueueStore
from router.core.routing_presets import resolve_routing_preset
from router.core.runtime_config import RuntimeConfig
from router.core.source_config import load_lane_configs


@dataclass(frozen=True, slots=True)
class QueueWorkerConfig:
    queue_dir: str
    output_dir: str
    batch_dir: str
    ledger_dir: str
    perf_log_path: str
    provider_state_path: str
    provider_lock_dir: str
    poll_interval_seconds: float = 2.0
    idle_exit_seconds: float | None = None
    worker_id: str = "worker"

    @classmethod
    def from_runtime(cls, runtime: RuntimeConfig, *, worker_id: str = "worker") -> "QueueWorkerConfig":
        return cls(
            queue_dir=runtime.queue_dir,
            output_dir=runtime.output_dir,
            batch_dir=runtime.batch_dir,
            ledger_dir=runtime.ledger_dir,
            perf_log_path=runtime.perf_log_path,
            provider_state_path=runtime.provider_state_path,
            provider_lock_dir=runtime.provider_lock_dir,
            worker_id=worker_id,
        )


class QueueWorker:
    def __init__(self, config: QueueWorkerConfig):
        self.config = config
        self.store = QueueStore(config.queue_dir)

    def run_once(self) -> dict[str, Any] | None:
        remote_task = self.store.claim_next_remote_task(worker_id=self.config.worker_id)
        if remote_task is not None:
            return self._poll_remote_task(remote_task)

        task = self._claim_scheduled_task()
        if task is None:
            return None
        task_id = str(task["task_id"])
        try:
            result = self._run_task(task)
        except Exception as exc:
            return self.store.fail_task(task_id, error=f"{type(exc).__name__}: {exc}")

        remote = self._remote_from_submit_result(task, result)
        if remote:
            return self.store.wait_remote_task(
                task_id,
                remote_async=remote,
                summary_path=result.summary_path,
                ledger_path=result.ledger_path,
                success_count=result.success_count,
                failure_count=result.failure_count,
            )

        if result.failure_count > 0:
            return self.store.fail_task(
                task_id,
                error=f"{result.failure_count} item(s) failed",
                summary_path=result.summary_path,
                ledger_path=result.ledger_path,
                success_count=result.success_count,
                failure_count=result.failure_count,
            )
        return self.store.complete_task(
            task_id,
            summary_path=result.summary_path,
            ledger_path=result.ledger_path,
            success_count=result.success_count,
            failure_count=result.failure_count,
        )

    def run_forever(self, *, max_tasks: int | None = None) -> list[dict[str, Any]]:
        processed: list[dict[str, Any]] = []
        idle_started = time.time()
        while True:
            row = self.run_once()
            if row is not None:
                processed.append(row)
                idle_started = time.time()
                if max_tasks is not None and len(processed) >= max_tasks:
                    return processed
                continue
            if self.config.idle_exit_seconds is not None and time.time() - idle_started >= self.config.idle_exit_seconds:
                return processed
            time.sleep(self.config.poll_interval_seconds)

    def _run_task(self, task: dict[str, Any]):
        preset = resolve_routing_preset(task.get("preset")) if task.get("preset") else None
        providers = (
            list(task.get("effective_providers") or [])
            or list(task.get("providers") or [])
            or (preset.providers if preset and preset.providers else None)
        )
        routing_policy = task.get("routing_policy") or (preset.routing_policy if preset else None)
        provider_tier = task.get("provider_tier") or (preset.provider_tier if preset else None)
        max_workers = task.get("max_workers")
        if max_workers is None and preset and preset.max_workers is not None:
            max_workers = preset.max_workers
        max_retries = task.get("max_retries_per_provider")
        if max_retries is None:
            max_retries = preset.max_retries_per_provider if preset and preset.max_retries_per_provider is not None else 1
        retry_delay = task.get("retry_delay_seconds")
        if retry_delay is None:
            retry_delay = preset.retry_delay_seconds if preset and preset.retry_delay_seconds is not None else 2.0
        return run_job_file(
            str(task["job_path"]),
            providers=providers,
            output_dir=task.get("output_dir") or self.config.output_dir,
            batch_dir=task.get("batch_dir") or self.config.batch_dir,
            ledger_dir=task.get("ledger_dir") or self.config.ledger_dir,
            perf_log_path=task.get("perf_log_file") or self.config.perf_log_path,
            provider_state_path=task.get("provider_state_file") or self.config.provider_state_path,
            default_profile=task.get("profile") or "generic",
            max_workers=max_workers,
            max_retries_per_provider=int(max_retries),
            retry_delay_seconds=float(retry_delay),
            routing_policy=routing_policy,
            provider_tier=provider_tier,
            preset_name=preset.name if preset else None,
            routing_preset=preset,
            provider_lock_dir=self.config.provider_lock_dir,
            cross_process_provider_locks=True,
            task_id=str(task["task_id"]),
            dry_run=False,
            async_submit_only=self._should_submit_async_remote(task, providers),
        )

    def _poll_remote_task(self, task: dict[str, Any]) -> dict[str, Any]:
        task_id = str(task["task_id"])
        remote = task.get("remote_async") if isinstance(task.get("remote_async"), dict) else {}
        provider = str(remote.get("provider") or "")
        remote_task_id = str(remote.get("task_id") or "")
        if not provider or not remote_task_id:
            return self.store.fail_task(task_id, error="remote_async metadata is missing provider or task_id")

        try:
            job_type, items = load_job_items_with_defaults(
                str(task["job_path"]),
                default_profile=task.get("profile") or "generic",
                default_routing_policy=task.get("routing_policy"),
                default_provider_tier=task.get("provider_tier"),
            )
            if len(items) != 1:
                return self.store.fail_task(task_id, error="remote_async polling supports exactly one item")
            item_id, request, _ = items[0]
            adapter = build_adapter(provider)
            poll_result = adapter.poll_async_result(remote_task_id, request)
            poll_result = write_result(
                task.get("output_dir") or self.config.output_dir,
                provider,
                request,
                poll_result,
                perf_log_path=task.get("perf_log_file") or self.config.perf_log_path,
            )
            summary_path, ledger_path, success_count, failure_count = self._write_remote_poll_summary(
                task,
                job_type=job_type,
                item_id=item_id,
                provider=provider,
                remote_task_id=remote_task_id,
                request_hash_value=request_hash(request),
                result=poll_result,
            )
        except Exception as exc:
            return self.store.fail_task(task_id, error=f"{type(exc).__name__}: {exc}")

        if poll_result.error_code == "async_pending":
            next_poll_at = time.time() + float(remote.get("poll_interval_seconds") or self.config.poll_interval_seconds)
            updated_remote = {
                **remote,
                "status": "pending",
                "next_poll_at": next_poll_at,
                "last_poll_at": time.time(),
                "last_summary_path": summary_path,
                "last_ledger_path": ledger_path,
            }
            return self.store.wait_remote_task(
                task_id,
                remote_async=updated_remote,
                summary_path=summary_path,
                ledger_path=ledger_path,
                success_count=success_count,
                failure_count=failure_count,
            )
        if failure_count > 0:
            return self.store.fail_task(
                task_id,
                error=poll_result.error_message or poll_result.error_code or "remote_async polling failed",
                summary_path=summary_path,
                ledger_path=ledger_path,
                success_count=success_count,
                failure_count=failure_count,
            )
        return self.store.complete_task(
            task_id,
            summary_path=summary_path,
            ledger_path=ledger_path,
            success_count=success_count,
            failure_count=failure_count,
        )

    def _write_remote_poll_summary(
        self,
        task: dict[str, Any],
        *,
        job_type: str,
        item_id: str,
        provider: str,
        remote_task_id: str,
        request_hash_value: str,
        result,
    ) -> tuple[str, str, int, int]:
        success = bool(result.ok)
        item = BatchItemResult(
            item_id=item_id,
            request_hash=request_hash_value,
            request_type=result.request_type,
            status="success" if success else "failed",
            selected_provider=provider,
            attempted_providers=[provider],
            attempts=[{
                "provider": provider,
                "retry_index": 0,
                "ok": success,
                "latency_ms": result.latency_ms,
                "timings_ms": dict(result.timings_ms or {}),
                "error_code": result.error_code,
                "error_message": result.error_message,
                "remote_task_id": remote_task_id,
            }],
            artifact_paths=list(result.artifact_paths),
            raw_response_path=result.raw_response_path,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            error_message=result.error_message,
        )
        run_dir = Path(task.get("batch_dir") or self.config.batch_dir) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_dir.mkdir(parents=True, exist_ok=True)
        summary_path = run_dir / "run-summary.json"
        success_count = 1 if success else 0
        failure_count = 0 if success else 1
        summary = {
            "schema_version": "batch-run-summary/v1",
            "dry_run": False,
            "job_type": job_type,
            "input_path": str(Path(task["job_path"]).resolve()),
            "success_count": success_count,
            "failure_count": failure_count,
            "providers_requested": [provider],
            "queue_task_id": task.get("task_id"),
            "remote_async": {
                "provider": provider,
                "task_id": remote_task_id,
                "status": "completed" if success else result.error_code,
            },
            "secret_values_included": False,
            "items": [asdict(item)],
        }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        ledger_path = write_batch_ledger(task.get("ledger_dir") or self.config.ledger_dir, summary)
        append_batch_summary(summary, perf_log_path=task.get("perf_log_file") or self.config.perf_log_path)
        return str(summary_path), str(ledger_path), success_count, failure_count

    def _should_submit_async_remote(self, task: dict[str, Any], providers: list[str] | None) -> bool:
        if not providers or len(providers) != 1:
            return False
        lanes = load_lane_configs()
        lane = lanes.get(providers[0])
        if not lane or not lane.async_remote:
            return False
        try:
            _, items = load_job_items_with_defaults(
                str(task["job_path"]),
                default_profile=task.get("profile") or "generic",
                default_routing_policy=task.get("routing_policy"),
                default_provider_tier=task.get("provider_tier"),
            )
        except Exception:
            return False
        return len(items) == 1

    def _remote_from_submit_result(self, task: dict[str, Any], result) -> dict[str, Any] | None:
        if result.failure_count != 1 or len(result.item_results) != 1:
            return None
        item = result.item_results[0]
        if item.error_code != "async_pending" or not item.attempts:
            return None
        attempt = item.attempts[-1]
        remote_task_id = attempt.get("remote_task_id")
        provider = attempt.get("provider") or item.selected_provider
        if not remote_task_id or not provider:
            return None
        current = time.time()
        return {
            "provider": str(provider),
            "task_id": str(remote_task_id),
            "status": "pending",
            "submitted_at": current,
            "next_poll_at": current + self.config.poll_interval_seconds,
            "poll_interval_seconds": self.config.poll_interval_seconds,
            "poll_attempts": 0,
            "submit_summary_path": result.summary_path,
            "submit_ledger_path": result.ledger_path,
        }

    def _claim_scheduled_task(self) -> dict[str, Any] | None:
        pending_count = len(self.store.list_tasks(statuses=["pending"]))
        retry_budget = max(3, pending_count + 1)
        for _ in range(retry_budget):
            decision = QueueScheduler(
                queue_dir=self.config.queue_dir,
                provider_lock_dir=self.config.provider_lock_dir,
                provider_state_path=self.config.provider_state_path,
            ).evaluate()
            for waiting in decision.waiting_tasks:
                self.store.update_pending_scheduling(
                    waiting.task_id,
                    scheduler_decision="waiting",
                    wait_reason=waiting.wait_reason,
                    selected_reason=waiting.selected_reason,
                    effective_providers=waiting.effective_providers,
                    provider_waits=[row.to_dict() for row in waiting.provider_waits],
                )
            if not decision.selected_task_id:
                return None
            task = self.store.claim_task(
                decision.selected_task_id,
                worker_id=self.config.worker_id,
                scheduler_decision=decision.selected_payload(),
            )
            if task is not None:
                return task
            # stale_scheduler_selection: another worker claimed it first; re-evaluate
            # within a bounded pending-count budget before idling.
        return None
