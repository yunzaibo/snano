from __future__ import annotations

import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any


QUEUE_TASK_SCHEMA_VERSION = "queue-task/v1"
QUEUE_STATUSES = ("pending", "running", "waiting_remote", "done", "failed", "cancelled")


class QueueStore:
    def __init__(self, queue_dir: str | Path):
        self.queue_dir = Path(queue_dir)
        for status in QUEUE_STATUSES:
            (self.queue_dir / status).mkdir(parents=True, exist_ok=True)

    def submit_task(
        self,
        job_file: str,
        *,
        preset: str | None = None,
        providers: list[str] | None = None,
        routing_policy: str | None = None,
        provider_tier: str | None = None,
        profile: str = "generic",
        max_workers: int | None = None,
        max_retries_per_provider: int | None = None,
        retry_delay_seconds: float | None = None,
        output_dir: str | None = None,
        batch_dir: str | None = None,
        ledger_dir: str | None = None,
        perf_log_file: str | None = None,
        provider_state_file: str | None = None,
        queue_priority: int | None = None,
        model_class: str | None = None,
        station_tier: str | None = None,
        latency_budget_seconds: int | None = None,
        created_by: str | None = None,
    ) -> dict[str, Any]:
        task_id = self._new_task_id()
        task_dir = self._task_dir("pending", task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        source = Path(job_file).expanduser().resolve()
        copied_job = task_dir / "job.json"
        shutil.copyfile(source, copied_job)
        now = time.time()
        record = {
            "schema_version": QUEUE_TASK_SCHEMA_VERSION,
            "task_id": task_id,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
            "created_by": created_by or f"pid:{os.getpid()}",
            "source_job_path": str(source),
            "job_path": str(copied_job),
            "preset": preset,
            "providers": list(providers or []),
            "routing_policy": routing_policy,
            "provider_tier": provider_tier,
            "profile": profile,
            "max_workers": max_workers,
            "max_retries_per_provider": max_retries_per_provider,
            "retry_delay_seconds": retry_delay_seconds,
            "output_dir": output_dir,
            "batch_dir": batch_dir,
            "ledger_dir": ledger_dir,
            "perf_log_file": perf_log_file,
            "provider_state_file": provider_state_file,
            "scheduling_policy": "speed_first_capacity_aware",
            "queue_priority": queue_priority,
            "model_class": model_class,
            "station_tier": station_tier,
            "latency_budget_seconds": latency_budget_seconds,
            "scheduler_decision": None,
            "wait_reason": None,
            "selected_reason": None,
            "effective_providers": [],
            "provider_waits": [],
            "remote_async": None,
            "last_scheduled_at": None,
            "scheduler_attempts": 0,
            "summary_path": None,
            "ledger_path": None,
            "success_count": None,
            "failure_count": None,
            "error": None,
            "secret_values_included": False,
        }
        self._write_record(task_dir, record)
        return dict(record)

    def claim_task(
        self,
        task_id: str,
        *,
        worker_id: str,
        scheduler_decision: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        current = time.time() if now is None else now
        pending_dir = self._task_dir("pending", task_id)
        if not pending_dir.exists():
            return None
        target = self._task_dir("running", task_id)
        try:
            pending_dir.replace(target)
        except OSError:
            return None
        record = self._read_record_from_dir(target)
        if not record:
            return None
        selected = scheduler_decision or {}
        record.update({
            "status": "running",
            "worker_id": worker_id,
            "claimed_at": current,
            "updated_at": current,
            "scheduler_decision": "selected",
            "wait_reason": None,
            "selected_reason": selected.get("selected_reason"),
            "effective_providers": list(selected.get("effective_providers") or []),
            "provider_waits": list(selected.get("provider_waits") or []),
            "last_scheduled_at": current,
            "scheduler_attempts": int(record.get("scheduler_attempts") or 0) + 1,
        })
        job_path = target / "job.json"
        record["job_path"] = str(job_path)
        self._write_record(target, record)
        return dict(record)

    def claim_next_remote_task(self, *, worker_id: str, now: float | None = None) -> dict[str, Any] | None:
        current = time.time() if now is None else now
        root = self.queue_dir / "waiting_remote"
        for waiting_dir in sorted(root.iterdir() if root.exists() else []):
            if not waiting_dir.is_dir():
                continue
            record = self._read_record_from_dir(waiting_dir)
            if not record:
                continue
            remote = record.get("remote_async") if isinstance(record.get("remote_async"), dict) else {}
            next_poll_at = float(remote.get("next_poll_at") or 0)
            if next_poll_at > current:
                continue
            target = self._task_dir("running", waiting_dir.name)
            try:
                waiting_dir.replace(target)
            except OSError:
                continue
            record.update({
                "status": "running",
                "worker_id": worker_id,
                "claimed_at": current,
                "updated_at": current,
                "wait_reason": None,
            })
            remote = dict(remote)
            remote["last_poll_claimed_at"] = current
            remote["poll_attempts"] = int(remote.get("poll_attempts") or 0) + 1
            record["remote_async"] = remote
            record["job_path"] = str(target / "job.json")
            self._write_record(target, record)
            return dict(record)
        return None

    def claim_next_task(self, *, worker_id: str, now: float | None = None) -> dict[str, Any] | None:
        current = time.time() if now is None else now
        for pending_dir in sorted((self.queue_dir / "pending").iterdir() if (self.queue_dir / "pending").exists() else []):
            if not pending_dir.is_dir():
                continue
            target = self._task_dir("running", pending_dir.name)
            try:
                pending_dir.replace(target)
            except OSError:
                continue
            record = self._read_record_from_dir(target)
            if not record:
                continue
            record.update({
                "status": "running",
                "worker_id": worker_id,
                "claimed_at": current,
                "updated_at": current,
            })
            job_path = target / "job.json"
            record["job_path"] = str(job_path)
            self._write_record(target, record)
            return dict(record)
        return None

    def update_pending_scheduling(
        self,
        task_id: str,
        *,
        scheduler_decision: str | None = None,
        wait_reason: str | None = None,
        selected_reason: str | None = None,
        effective_providers: list[str] | None = None,
        provider_waits: list[dict[str, Any]] | None = None,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        task_dir = self._task_dir("pending", task_id)
        if not task_dir.exists():
            return None
        record = self._read_record_from_dir(task_dir)
        if not record:
            return None
        current = time.time() if now is None else now
        record.update({
            "updated_at": current,
            "scheduler_decision": scheduler_decision,
            "wait_reason": wait_reason,
            "selected_reason": selected_reason,
            "effective_providers": list(effective_providers or []),
            "provider_waits": list(provider_waits or []),
            "last_scheduled_at": current,
            "scheduler_attempts": int(record.get("scheduler_attempts") or 0) + 1,
        })
        try:
            self._write_record(task_dir, record)
        except FileNotFoundError:
            return None
        return dict(record)

    def task_status(self, task_id: str) -> dict[str, Any] | None:
        path = self._find_task_dir(task_id)
        if not path:
            return None
        return self._read_record_from_dir(path)

    def list_tasks(self, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        wanted = statuses or list(QUEUE_STATUSES)
        rows: list[dict[str, Any]] = []
        for status in wanted:
            if status not in QUEUE_STATUSES:
                continue
            root = self.queue_dir / status
            if not root.exists():
                continue
            for task_dir in sorted(root.iterdir()):
                if not task_dir.is_dir():
                    continue
                record = self._read_record_from_dir(task_dir)
                if record:
                    rows.append(record)
        rows.sort(key=lambda row: (float(row.get("created_at") or 0), str(row.get("task_id") or "")))
        return rows

    def cancel_task(self, task_id: str) -> dict[str, Any] | None:
        task_dir = self._task_dir("pending", task_id)
        if not task_dir.exists():
            return None
        target = self._task_dir("cancelled", task_id)
        task_dir.replace(target)
        record = self._read_record_from_dir(target)
        if not record:
            return None
        record.update({"status": "cancelled", "updated_at": time.time(), "error": "cancelled before execution"})
        record["job_path"] = str(target / "job.json")
        self._write_record(target, record)
        return dict(record)

    def complete_task(
        self,
        task_id: str,
        *,
        summary_path: str,
        ledger_path: str,
        success_count: int,
        failure_count: int,
    ) -> dict[str, Any]:
        task_dir = self._task_dir("running", task_id)
        target = self._task_dir("done", task_id)
        task_dir.replace(target)
        record = self._read_record_from_dir(target) or {}
        record.update({
            "status": "done",
            "updated_at": time.time(),
            "completed_at": time.time(),
            "summary_path": summary_path,
            "ledger_path": ledger_path,
            "success_count": success_count,
            "failure_count": failure_count,
            "error": None,
            "job_path": str(target / "job.json"),
        })
        self._write_record(target, record)
        return dict(record)

    def wait_remote_task(
        self,
        task_id: str,
        *,
        remote_async: dict[str, Any],
        summary_path: str | None = None,
        ledger_path: str | None = None,
        success_count: int | None = None,
        failure_count: int | None = None,
    ) -> dict[str, Any]:
        task_dir = self._task_dir("running", task_id)
        target = self._task_dir("waiting_remote", task_id)
        task_dir.replace(target)
        record = self._read_record_from_dir(target) or {}
        record.update({
            "status": "waiting_remote",
            "updated_at": time.time(),
            "summary_path": summary_path,
            "ledger_path": ledger_path,
            "success_count": success_count,
            "failure_count": failure_count,
            "error": None,
            "remote_async": dict(remote_async),
            "wait_reason": "remote_async_pending",
            "job_path": str(target / "job.json"),
        })
        self._write_record(target, record)
        return dict(record)

    def fail_task(
        self,
        task_id: str,
        *,
        error: str,
        summary_path: str | None = None,
        ledger_path: str | None = None,
        success_count: int | None = None,
        failure_count: int | None = None,
    ) -> dict[str, Any]:
        task_dir = self._task_dir("running", task_id)
        target = self._task_dir("failed", task_id)
        task_dir.replace(target)
        record = self._read_record_from_dir(target) or {}
        record.update({
            "status": "failed",
            "updated_at": time.time(),
            "completed_at": time.time(),
            "summary_path": summary_path,
            "ledger_path": ledger_path,
            "success_count": success_count,
            "failure_count": failure_count,
            "error": error,
            "job_path": str(target / "job.json"),
        })
        self._write_record(target, record)
        return dict(record)

    def _task_dir(self, status: str, task_id: str) -> Path:
        return self.queue_dir / status / task_id

    def _find_task_dir(self, task_id: str) -> Path | None:
        for status in QUEUE_STATUSES:
            path = self._task_dir(status, task_id)
            if path.exists():
                return path
        return None

    def _new_task_id(self) -> str:
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        return f"Q-{stamp}-{uuid.uuid4().hex[:8]}"

    def _write_record(self, task_dir: Path, record: dict[str, Any]) -> None:
        record_path = task_dir / "task.json"
        tmp = task_dir / f"task.{os.getpid()}.{uuid.uuid4().hex}.json.tmp"
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(record_path)

    def _read_record_from_dir(self, task_dir: Path) -> dict[str, Any] | None:
        record_path = task_dir / "task.json"
        if not record_path.exists():
            return None
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        if data.get("schema_version") != QUEUE_TASK_SCHEMA_VERSION:
            return None
        return data
