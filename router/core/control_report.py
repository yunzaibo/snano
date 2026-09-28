from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from router.core.queue_store import QueueStore
from router.core.readiness import build_readiness_report
from router.core.redaction import redact_value
from router.core.remediation_events import remediation_summary


CONTROL_REPORT_SCHEMA_VERSION = "control-report/v1"


def _read_jsonl(path: str | None) -> list[dict[str, Any]]:
    if not path:
        return []
    candidate = Path(path)
    if not candidate.exists():
        return []
    files = sorted(candidate.glob("*.jsonl")) if candidate.is_dir() else [candidate]
    rows: list[dict[str, Any]] = []
    for file_path in files:
        if not file_path.exists():
            continue
        for line in file_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _recent_batch_summaries(events: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    batches = [event for event in events if event.get("event") == "batch_summary"]
    return [
        redact_value({
            "recorded_at": event.get("recorded_at"),
            "schema_version": event.get("schema_version"),
            "dry_run": event.get("dry_run"),
            "job_type": event.get("job_type"),
            "success_count": event.get("success_count"),
            "failure_count": event.get("failure_count"),
            "items_total": event.get("items_total"),
            "attempt_count": event.get("attempt_count"),
            "provider_status_counts": event.get("provider_status_counts"),
            "remediation_summary": event.get("remediation_summary"),
            "remediation_events": event.get("remediation_events") or [],
        })
        for event in batches[-max(0, limit):]
    ]


def _dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in seen:
            continue
        seen.add(event_id)
        deduped.append(event)
    return deduped


def _queue_snapshot(queue_dir: str | None, task_id: str | None) -> dict[str, Any]:
    if not queue_dir:
        return {
            "enabled": False,
            "queue_missing": True,
            "tasks": [],
            "task": None,
        }
    queue_path = Path(queue_dir)
    if not queue_path.exists():
        return redact_value({
            "enabled": False,
            "queue_missing": True,
            "queue_dir": queue_dir,
            "status_counts": {},
            "tasks": [],
            "task": None,
        })
    store = QueueStore(queue_dir)
    tasks = store.list_tasks()
    status_counts: dict[str, int] = {}
    for task in tasks:
        status = str(task.get("status") or "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
    return redact_value({
        "enabled": True,
        "queue_missing": False,
        "queue_dir": queue_dir,
        "status_counts": status_counts,
        "tasks": tasks,
        "task": store.task_status(task_id) if task_id else None,
    })


def _recommended_next_steps(events: list[dict[str, Any]], queue: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    if any(event.get("approval_required") for event in events):
        steps.append("先处理 approval_required=true 的项目；不要把 API key 贴到对话或日志里。")
    if any(event.get("event_type") == "credential" for event in events):
        steps.append("检查对应 provider 的 credential 配置是否存在、是否过期或被风控。")
    if any(event.get("event_type") == "quota_or_balance" for event in events):
        steps.append("由人工确认余额、额度或套餐状态；本地 agent 不自动充值或修改账户。")
    if any(event.get("event_type") == "rate_limit" for event in events):
        steps.append("降低并发、等待 cooldown，或把任务切到其他可用 provider group。")
    if any(event.get("event_type") == "adapter_drift" for event in events):
        steps.append("检查 adapter 响应解析、异步轮询或图片 artifact 质量契约。")
    status_counts = queue.get("status_counts") if isinstance(queue, dict) else {}
    if isinstance(status_counts, dict) and int(status_counts.get("pending") or 0) > 0:
        steps.append("队列仍有 pending 任务；查看 wait_reason 和 provider_waits 判断是否被容量或 cooldown 挡住。")
    if not steps:
        steps.append("暂无必须人工处理的 remediation；优先查看最近 batch summary 的失败 provider 和延迟。")
    return steps


def build_control_report(
    *,
    perf_log_path: str | None = None,
    provider_state_path: str | None = None,
    queue_dir: str | None = None,
    task_id: str | None = None,
    providers: list[str] | None = None,
    latency_budget_seconds: int | None = None,
    recent_batch_limit: int = 5,
) -> dict[str, Any]:
    readiness = build_readiness_report(
        perf_log_path=perf_log_path,
        provider_state_path=provider_state_path,
        providers=providers,
        latency_budget_seconds=latency_budget_seconds,
    )
    perf_events = _read_jsonl(perf_log_path)
    recent_batches = _recent_batch_summaries(perf_events, recent_batch_limit)
    remediation_events = _dedupe_events(
        list(readiness.get("remediation_events") or [])
        + [
            event
            for batch in recent_batches
            for event in list(batch.get("remediation_events") or [])
        ]
    )
    queue = _queue_snapshot(queue_dir, task_id)
    return {
        "schema_version": CONTROL_REPORT_SCHEMA_VERSION,
        "generated_at": time.time(),
        "network_calls": 0,
        "secret_values_included": False,
        "perf_log_path": perf_log_path,
        "provider_state_path": provider_state_path,
        "readiness": readiness,
        "queue": queue,
        "recent_batch_summaries": recent_batches,
        "remediation_events": remediation_events,
        "remediation_summary": remediation_summary(remediation_events),
        "recommended_next_steps": _recommended_next_steps(remediation_events, queue),
    }
