from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from router.core.batch_runner import load_job_items_with_defaults
from router.core.models import CapabilityBucket, GenerationRequest, ProviderCapability
from router.core.provider_locks import ProviderLeaseManager
from router.core.provider_contracts import check_provider_capability
from router.core.provider_registry import (
    provider_capability_registry,
    provider_group_for,
    provider_startup_diagnostics,
)
from router.core.provider_state_store import ProviderHealthStore
from router.core.queue_store import QueueStore
from router.core.routing_presets import resolve_routing_preset
from router.core.scheduler import ProviderState


SCHEDULING_POLICY = "speed_first_capacity_aware"

DEFAULT_PRIORITY_BY_PRESET = {
    "speed_preview": 10,
    "pro_primary": 30,
    "internal_balanced": 35,
    "batch_draft": 60,
    "batch_draft_experimental": 80,
}


@dataclass(slots=True)
class ProviderWait:
    provider: str
    wait_reason: str
    provider_group: str | None = None
    active_leases: int = 0
    active_queue_tasks: int = 0
    max_concurrency: int = 1
    cooldown_until: float | None = None
    cooldown_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(slots=True)
class QueueTaskDecision:
    task_id: str
    status: str
    queue_priority: int
    effective_priority: int
    created_at: float
    preset: str | None = None
    model_class: str | None = None
    station_tier: str | None = None
    latency_budget_seconds: int | None = None
    selected_reason: str | None = None
    wait_reason: str | None = None
    effective_providers: list[str] = field(default_factory=list)
    provider_waits: list[ProviderWait] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        row = asdict(self)
        row["provider_waits"] = [wait.to_dict() for wait in self.provider_waits]
        return row


@dataclass(slots=True)
class QueueScheduleDecision:
    selected_task_id: str | None
    selected_task: dict[str, Any] | None
    effective_providers: list[str] = field(default_factory=list)
    selected_reason: str | None = None
    waiting_tasks: list[QueueTaskDecision] = field(default_factory=list)
    task_decisions: list[QueueTaskDecision] = field(default_factory=list)
    generated_at: float = field(default_factory=time.time)
    secret_values_included: bool = False

    def selected_payload(self) -> dict[str, Any]:
        return {
            "selected_reason": self.selected_reason,
            "effective_providers": list(self.effective_providers),
            "provider_waits": (
                self.selected_decision().to_dict().get("provider_waits", [])
                if self.selected_decision()
                else []
            ),
        }

    def selected_decision(self) -> QueueTaskDecision | None:
        if not self.selected_task_id:
            return None
        for decision in self.task_decisions:
            if decision.task_id == self.selected_task_id:
                return decision
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_task_id": self.selected_task_id,
            "effective_providers": list(self.effective_providers),
            "selected_reason": self.selected_reason,
            "waiting_tasks": [row.to_dict() for row in self.waiting_tasks],
            "task_decisions": [row.to_dict() for row in self.task_decisions],
            "generated_at": self.generated_at,
            "secret_values_included": self.secret_values_included,
        }


@dataclass(slots=True)
class _TaskSchedulingContext:
    request: GenerationRequest | None = None
    item_providers: list[str] = field(default_factory=list)
    parse_error: str | None = None


@dataclass(slots=True)
class _QueueLoad:
    provider_counts: dict[str, int] = field(default_factory=dict)
    pool_counts: dict[str, int] = field(default_factory=dict)


class QueueScheduler:
    def __init__(
        self,
        *,
        queue_dir: str | Path,
        provider_lock_dir: str | Path,
        provider_state_path: str | Path | None = None,
        aging_seconds: float = 300.0,
    ):
        self.store = QueueStore(queue_dir)
        self.lease_manager = ProviderLeaseManager(provider_lock_dir)
        self.provider_state_path = str(provider_state_path) if provider_state_path else None
        self.aging_seconds = aging_seconds

    def evaluate(self, *, now: float | None = None) -> QueueScheduleDecision:
        current = time.time() if now is None else now
        pending = self.store.list_tasks(statuses=["pending"])
        contexts = {
            str(task.get("task_id") or ""): self._task_context(task)
            for task in pending
        }
        all_candidates = sorted({
            provider
            for task in pending
            for provider in self._candidate_providers(task, contexts.get(str(task.get("task_id") or "")))
        })
        diagnostics = self._diagnostics()
        queue_load = self._active_queue_load(diagnostics, now=current)
        registry = self._registry(all_candidates)
        states = self._states(all_candidates)

        decisions = [
            self._evaluate_task(
                task,
                contexts.get(str(task.get("task_id") or "")),
                diagnostics,
                queue_load,
                registry,
                states,
                current,
            )
            for task in pending
        ]
        runnable = [row for row in decisions if row.status == "runnable"]
        runnable.sort(key=lambda row: (row.effective_priority, row.created_at, row.task_id))
        selected = runnable[0] if runnable else None

        waiting: list[QueueTaskDecision] = []
        for row in decisions:
            if selected and row.task_id == selected.task_id:
                continue
            if row.status == "runnable":
                row.wait_reason = "lower_priority_runnable_exists"
            waiting.append(row)

        return QueueScheduleDecision(
            selected_task_id=selected.task_id if selected else None,
            selected_task=(
                next((task for task in pending if task.get("task_id") == selected.task_id), None)
                if selected
                else None
            ),
            effective_providers=list(selected.effective_providers) if selected else [],
            selected_reason=selected.selected_reason if selected else None,
            waiting_tasks=waiting,
            task_decisions=decisions,
            generated_at=current,
            secret_values_included=False,
        )

    def _evaluate_task(
        self,
        task: dict[str, Any],
        context: _TaskSchedulingContext | None,
        diagnostics: dict[str, dict[str, Any]],
        queue_load: _QueueLoad,
        registry: dict[str, ProviderCapability],
        states: dict[str, ProviderState],
        now: float,
    ) -> QueueTaskDecision:
        task_id = str(task.get("task_id") or "")
        candidates = self._candidate_providers(task, context)
        queue_priority = self._queue_priority(task)
        effective_priority = self._effective_priority(task, queue_priority, now)
        request = context.request if context else None
        provider_waits: list[ProviderWait] = []
        runnable: list[str] = []

        if not candidates:
            selected_reason = "fifo_runnable"
            if effective_priority < queue_priority:
                selected_reason = "aged_priority"
            elif queue_priority <= 10:
                selected_reason = "speed_priority"
            return QueueTaskDecision(
                task_id=task_id,
                status="runnable",
                queue_priority=queue_priority,
                effective_priority=effective_priority,
                created_at=float(task.get("created_at") or 0.0),
                preset=task.get("preset"),
                model_class=task.get("model_class"),
                station_tier=task.get("station_tier"),
                latency_budget_seconds=self._latency_budget(task),
                selected_reason=selected_reason,
                wait_reason=None,
                effective_providers=[],
                provider_waits=[],
            )

        for provider in candidates:
            wait = self._provider_wait(provider, task, request, diagnostics, queue_load, registry, states, now)
            if wait is None:
                runnable.append(provider)
            else:
                provider_waits.append(wait)

        selected_reason = None
        status = "waiting"
        wait_reason = self._aggregate_wait_reason(provider_waits, candidates)
        effective_providers: list[str] = []
        race_capacity_blocked = self._speed_first_race_capacity_blocked(
            task,
            candidates,
            provider_waits,
        )
        if runnable and not (self._routing_policy(task) == "speed_first" and race_capacity_blocked):
            status = "runnable"
            wait_reason = None
            effective_providers = list(candidates)
            first_runnable = runnable[0]
            if first_runnable != candidates[0]:
                effective_providers = [first_runnable] + [name for name in candidates if name != first_runnable]
                selected_reason = "fallback_provider_available"
            elif effective_priority < queue_priority:
                selected_reason = "aged_priority"
            elif queue_priority <= 10:
                selected_reason = "speed_priority"
            else:
                selected_reason = "primary_provider_available"

        return QueueTaskDecision(
            task_id=task_id,
            status=status,
            queue_priority=queue_priority,
            effective_priority=effective_priority,
            created_at=float(task.get("created_at") or 0.0),
            preset=task.get("preset"),
            model_class=task.get("model_class"),
            station_tier=task.get("station_tier"),
            latency_budget_seconds=self._latency_budget(task),
            selected_reason=selected_reason,
            wait_reason=wait_reason,
            effective_providers=effective_providers,
            provider_waits=provider_waits,
        )

    def _provider_wait(
        self,
        provider: str,
        task: dict[str, Any],
        request: GenerationRequest | None,
        diagnostics: dict[str, dict[str, Any]],
        queue_load: _QueueLoad,
        registry: dict[str, ProviderCapability],
        states: dict[str, ProviderState],
        now: float,
    ) -> ProviderWait | None:
        diagnostic = diagnostics.get(provider)
        cap = registry.get(provider) or self._default_capability(provider)
        state = states.get(provider) or ProviderState()
        provider_group = provider_group_for(provider)
        raw_max_concurrency = cap.max_concurrency
        if diagnostic is not None and diagnostic.get("max_concurrency") is not None:
            raw_max_concurrency = int(diagnostic.get("max_concurrency") or raw_max_concurrency or 1)
        max_concurrency = max(1, int(raw_max_concurrency or 1))
        active_leases = len(self.lease_manager.active_leases(provider))
        pool_identity = str(diagnostic.get("pool_identity") or "") if diagnostic else ""
        active_queue_tasks = queue_load.provider_counts.get(provider, 0)
        if pool_identity:
            active_queue_tasks = max(active_queue_tasks, queue_load.pool_counts.get(pool_identity, 0))
        active_load = max(active_leases, active_queue_tasks)

        if diagnostic is not None and not bool(diagnostic.get("enabled", True)):
            return ProviderWait(provider, "no_provider_available", provider_group, active_leases, active_queue_tasks, max_concurrency)
        if diagnostic is not None and not bool(diagnostic.get("key_present", True)):
            return ProviderWait(provider, "missing_credential", provider_group, active_leases, active_queue_tasks, max_concurrency)
        if state.cooldown_until > now:
            return ProviderWait(
                provider,
                "provider_cooldown",
                provider_group,
                active_leases,
                active_queue_tasks,
                max_concurrency,
                cooldown_until=state.cooldown_until,
                cooldown_reason=state.cooldown_reason,
            )
        if not self._supports_request(cap, request):
            return ProviderWait(provider, "unsupported_request", provider_group, active_leases, active_queue_tasks, max_concurrency)
        if active_load >= max_concurrency:
            reason = (
                "race_group_capacity_blocked"
                if self._routing_policy(task) == "speed_first"
                else "provider_capacity_full"
            )
            return ProviderWait(provider, reason, provider_group, active_leases, active_queue_tasks, max_concurrency)
        return None

    def _candidate_providers(
        self,
        task: dict[str, Any],
        context: _TaskSchedulingContext | None = None,
    ) -> list[str]:
        providers = [str(name).strip().lower() for name in list(task.get("providers") or []) if str(name).strip()]
        if providers:
            return providers
        preset_name = task.get("preset")
        if preset_name:
            preset = resolve_routing_preset(str(preset_name))
            if preset and preset.providers:
                return [provider.strip().lower() for provider in preset.providers if provider.strip()]
        if context and context.request:
            return [str(name).strip().lower() for name in context.item_providers if str(name).strip()]
        return []

    def _task_context(self, task: dict[str, Any]) -> _TaskSchedulingContext:
        try:
            _, items = load_job_items_with_defaults(
                str(task["job_path"]),
                default_profile=str(task.get("profile") or "generic"),
                default_routing_policy=task.get("routing_policy"),
                default_provider_tier=task.get("provider_tier"),
            )
        except Exception as exc:
            return _TaskSchedulingContext(parse_error=f"{type(exc).__name__}: {exc}")
        if not items:
            return _TaskSchedulingContext()
        return _TaskSchedulingContext(
            request=items[0][1],
            item_providers=list(items[0][2] or []),
        )

    def _queue_priority(self, task: dict[str, Any]) -> int:
        raw = task.get("queue_priority")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                pass
        preset = str(task.get("preset") or "").strip().lower()
        return DEFAULT_PRIORITY_BY_PRESET.get(preset, 50)

    def _latency_budget(self, task: dict[str, Any]) -> int | None:
        raw = task.get("latency_budget_seconds")
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
        preset_name = task.get("preset")
        if not preset_name:
            return None
        preset = resolve_routing_preset(str(preset_name))
        return preset.latency_budget_seconds if preset else None

    def _effective_priority(self, task: dict[str, Any], queue_priority: int, now: float) -> int:
        created_at = float(task.get("created_at") or now)
        if now - created_at >= self.aging_seconds:
            return min(queue_priority, 0)
        return queue_priority

    def _active_queue_load(self, diagnostics: dict[str, dict[str, Any]], *, now: float) -> _QueueLoad:
        provider_counts: dict[str, int] = {}
        pool_counts: dict[str, int] = {}
        for task in self.store.list_tasks(statuses=["running", "waiting_remote"]):
            providers = self._active_task_providers(task, now=now)
            for provider in providers:
                provider_counts[provider] = provider_counts.get(provider, 0) + 1
                pool_identity = diagnostics.get(provider, {}).get("pool_identity")
                if pool_identity:
                    pool = str(pool_identity)
                    pool_counts[pool] = pool_counts.get(pool, 0) + 1
        return _QueueLoad(provider_counts=provider_counts, pool_counts=pool_counts)

    def _active_task_providers(self, task: dict[str, Any], *, now: float) -> list[str]:
        remote = task.get("remote_async") if isinstance(task.get("remote_async"), dict) else None
        if remote and remote.get("provider"):
            return [str(remote["provider"]).strip().lower()]

        status = str(task.get("status") or "")
        if status != "running":
            return []

        providers = [
            str(name).strip().lower()
            for name in list(task.get("effective_providers") or task.get("providers") or [])
            if str(name).strip()
        ]
        if not providers:
            return []

        claimed_at = float(task.get("claimed_at") or 0.0)
        if claimed_at and now - claimed_at > 15.0:
            return []

        if self._routing_policy(task) == "speed_first":
            selected: list[str] = []
            seen_groups: set[str] = set()
            for provider in providers:
                group = provider_group_for(provider)
                if group in seen_groups:
                    continue
                seen_groups.add(group)
                selected.append(provider)
            return selected
        return [providers[0]]

    def _routing_policy(self, task: dict[str, Any]) -> str | None:
        if task.get("routing_policy"):
            return str(task["routing_policy"]).strip().lower()
        preset_name = task.get("preset")
        if not preset_name:
            return None
        preset = resolve_routing_preset(str(preset_name))
        return preset.routing_policy.strip().lower() if preset and preset.routing_policy else None

    def _diagnostics(self) -> dict[str, dict[str, Any]]:
        return {str(row.get("provider")): row for row in provider_startup_diagnostics()}

    def _registry(self, providers: list[str]) -> dict[str, ProviderCapability]:
        registry = provider_capability_registry(providers)
        for provider in providers:
            registry.setdefault(provider, self._default_capability(provider))
        return registry

    def _states(self, providers: list[str]) -> dict[str, ProviderState]:
        if not self.provider_state_path:
            return {provider: ProviderState() for provider in providers}
        store = ProviderHealthStore(self.provider_state_path)
        return store.load(providers)

    def _default_capability(self, provider: str) -> ProviderCapability:
        return ProviderCapability(
            provider=provider,
            bucket=CapabilityBucket.MULTI_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=True,
            max_reference_images=5,
            max_concurrency=1,
            enabled=True,
        )

    def _supports_request(self, cap: ProviderCapability, request: GenerationRequest | None) -> bool:
        if request is None:
            return True
        return check_provider_capability(cap, request).supported

    def _speed_first_race_capacity_blocked(
        self,
        task: dict[str, Any],
        candidates: list[str],
        waits: list[ProviderWait],
    ) -> bool:
        if self._routing_policy(task) != "speed_first":
            return False
        deduped_race_providers = self._deduped_speed_first_race_providers(candidates, waits)
        return any(
            wait.provider in deduped_race_providers and wait.wait_reason == "race_group_capacity_blocked"
            for wait in waits
        )

    def _deduped_speed_first_race_providers(
        self,
        candidates: list[str],
        waits: list[ProviderWait],
    ) -> set[str]:
        waits_by_provider = {wait.provider: wait for wait in waits}
        selected: set[str] = set()
        seen_groups: set[str] = set()
        for provider in candidates:
            wait = waits_by_provider.get(provider)
            if wait and wait.wait_reason in {
                "missing_credential",
                "provider_cooldown",
                "unsupported_request",
                "no_provider_available",
            }:
                continue
            group = provider_group_for(provider)
            if group in seen_groups:
                continue
            seen_groups.add(group)
            selected.add(provider)
        return selected

    def _aggregate_wait_reason(self, waits: list[ProviderWait], candidates: list[str]) -> str:
        if not candidates:
            return "no_provider_available"
        reasons = [wait.wait_reason for wait in waits]
        for reason in (
            "missing_credential",
            "provider_cooldown",
            "provider_capacity_full",
            "race_group_capacity_blocked",
            "unsupported_request",
            "no_provider_available",
        ):
            if reason in reasons:
                return reason
        return "no_provider_available"
