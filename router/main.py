from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from router.core.batch_runner import run_job_file
from router.core.benchmark import BenchmarkConfig, DEFAULT_BENCHMARK_PROMPT, run_internal_benchmark
from router.core.control_report import build_control_report
from router.core.job_templates import list_job_templates
from router.core.queue_store import QueueStore
from router.core.queue_worker import QueueWorker, QueueWorkerConfig
from router.core.readiness import build_readiness_report
from router.core.remediation_actions import propose_remediation_action
from router.core.remediation_audit import append_remediation_audit
from router.core.routing_presets import resolve_routing_preset
from router.core.runtime_config import RuntimeConfig


DEFAULT_CONFIG = RuntimeConfig.default()


def main() -> None:
    parser = argparse.ArgumentParser(description="Multi-image-router batch entry")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run a manifest/variants job file")
    explain_parser = subparsers.add_parser("explain-plan", help="Dry-run a job and explain routing without provider calls")
    templates_parser = subparsers.add_parser("templates", help="List reusable job templates")
    readiness_parser = subparsers.add_parser("readiness", help="Print a no-network provider/lane readiness report")
    benchmark_parser = subparsers.add_parser("benchmark", help="Run real small-image probes into an isolated benchmark log")
    submit_parser = subparsers.add_parser("submit", help="Submit a job to the local async queue")
    status_parser = subparsers.add_parser("status", help="Show one queued task status")
    list_queue_parser = subparsers.add_parser("list-queue", help="List local async queue tasks")
    cancel_parser = subparsers.add_parser("cancel", help="Cancel a pending queued task")
    worker_parser = subparsers.add_parser("worker", help="Run a local queue worker")
    control_report_parser = subparsers.add_parser("control-report", help="Print local agent-readable control-plane evidence")
    remediation_action_parser = subparsers.add_parser("remediation-action", help="Propose and audit a bounded remediation action")

    for job_parser in (run_parser, explain_parser):
        job_parser.add_argument("job_file", help="Path to manifest/variants JSON file")
        job_parser.add_argument("--preset", help="Routing preset name, for example pro_primary or safe_apiyi_only")
        job_parser.add_argument("--provider", action="append", default=[], help="Restrict to one or more providers, in priority order")
        job_parser.add_argument("--output-dir", default=DEFAULT_CONFIG.output_dir)
        job_parser.add_argument("--batch-dir", default=DEFAULT_CONFIG.batch_dir)
        job_parser.add_argument("--ledger-dir", default=DEFAULT_CONFIG.ledger_dir)
        job_parser.add_argument("--perf-log-file", default=DEFAULT_CONFIG.perf_log_path)
        job_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)
        job_parser.add_argument("--max-workers", type=int, default=None, help="Maximum parallel workers; 0 means auto")
        job_parser.add_argument("--max-retries-per-provider", type=int, default=None, help="Retries on the same provider before fallback")
        job_parser.add_argument("--retry-delay-seconds", type=float, default=None, help="Delay between same-provider retries")
        job_parser.add_argument("--routing-policy", default=None, help="Override routing policy for all items")
        job_parser.add_argument("--provider-tier", default=None, help="Override source tier, for example professional or daily")
        job_parser.add_argument("--profile", default="generic")
    run_parser.add_argument("--dry-run", action="store_true", help="Write an explain plan without provider calls")

    templates_parser.add_argument("--json", action="store_true", help="Print templates as JSON")
    readiness_parser.add_argument("--provider", action="append", default=[], help="Restrict to one or more providers/lanes")
    readiness_parser.add_argument("--preset", help="Restrict readiness to providers/lanes from a routing preset")
    readiness_parser.add_argument("--perf-log-file", default=DEFAULT_CONFIG.perf_log_path)
    readiness_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)

    control_report_parser.add_argument("--provider", action="append", default=[], help="Restrict to one or more providers/lanes")
    control_report_parser.add_argument("--preset", help="Restrict report to providers/lanes from a routing preset")
    control_report_parser.add_argument("--perf-log-file", default=DEFAULT_CONFIG.perf_log_path)
    control_report_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)
    control_report_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)
    control_report_parser.add_argument("--task-id", default=None)
    control_report_parser.add_argument("--recent-batch-limit", type=int, default=5)

    remediation_action_parser.add_argument("--event-json", required=True, help="JSON object from remediation_events[]")
    remediation_action_parser.add_argument("--action", default=None, help="Override action to propose")
    remediation_action_parser.add_argument("--approved", action="store_true", help="Mark human approval as already granted")
    remediation_action_parser.add_argument("--actor", default="operator")
    remediation_action_parser.add_argument("--audit-log-file", default=DEFAULT_CONFIG.remediation_audit_path)

    benchmark_parser.add_argument("--preset", help="Benchmark providers from a routing preset")
    benchmark_parser.add_argument("--provider", action="append", default=[], help="Benchmark one or more providers/lanes")
    benchmark_parser.add_argument("--samples", type=int, default=1, help="Samples per provider, clamped to 1-3")
    benchmark_parser.add_argument("--prompt", default=DEFAULT_BENCHMARK_PROMPT)
    benchmark_parser.add_argument("--profile", default="generic")
    benchmark_parser.add_argument("--output-dir", default=str(PROJECT_ROOT / "data" / "artifacts" / "internal-benchmark"))
    benchmark_parser.add_argument("--batch-dir", default=str(PROJECT_ROOT / "data" / "batches" / "internal-benchmark"))
    benchmark_parser.add_argument("--ledger-dir", default=str(PROJECT_ROOT / "data" / "ledger" / "internal-benchmark"))
    benchmark_parser.add_argument("--perf-log-file", default=None, help="Defaults to logs/internal-benchmark-<timestamp>.jsonl")
    benchmark_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)

    submit_parser.add_argument("job_file")
    submit_parser.add_argument("--preset", help="Routing preset name")
    submit_parser.add_argument("--provider", action="append", default=[], help="Restrict to one or more providers")
    submit_parser.add_argument("--routing-policy", default=None)
    submit_parser.add_argument("--provider-tier", default=None)
    submit_parser.add_argument("--profile", default="generic")
    submit_parser.add_argument("--queue-priority", type=int, default=None)
    submit_parser.add_argument("--model-class", default=None)
    submit_parser.add_argument("--station-tier", default=None)
    submit_parser.add_argument("--latency-budget-seconds", type=int, default=None)
    submit_parser.add_argument("--max-workers", type=int, default=None)
    submit_parser.add_argument("--max-retries-per-provider", type=int, default=None)
    submit_parser.add_argument("--retry-delay-seconds", type=float, default=None)
    submit_parser.add_argument("--output-dir", default=DEFAULT_CONFIG.output_dir)
    submit_parser.add_argument("--batch-dir", default=DEFAULT_CONFIG.batch_dir)
    submit_parser.add_argument("--ledger-dir", default=DEFAULT_CONFIG.ledger_dir)
    submit_parser.add_argument("--perf-log-file", default=DEFAULT_CONFIG.perf_log_path)
    submit_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)
    submit_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)

    status_parser.add_argument("task_id")
    status_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)
    list_queue_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)
    list_queue_parser.add_argument("--status", action="append", default=[])
    cancel_parser.add_argument("task_id")
    cancel_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)

    worker_parser.add_argument("--queue-dir", default=DEFAULT_CONFIG.queue_dir)
    worker_parser.add_argument("--provider-lock-dir", default=DEFAULT_CONFIG.provider_lock_dir)
    worker_parser.add_argument("--output-dir", default=DEFAULT_CONFIG.output_dir)
    worker_parser.add_argument("--batch-dir", default=DEFAULT_CONFIG.batch_dir)
    worker_parser.add_argument("--ledger-dir", default=DEFAULT_CONFIG.ledger_dir)
    worker_parser.add_argument("--perf-log-file", default=DEFAULT_CONFIG.perf_log_path)
    worker_parser.add_argument("--provider-state-file", default=DEFAULT_CONFIG.provider_state_path)
    worker_parser.add_argument("--worker-id", default="worker")
    worker_parser.add_argument("--once", action="store_true")
    worker_parser.add_argument("--max-tasks", type=int, default=None)
    worker_parser.add_argument("--poll-interval-seconds", type=float, default=2.0)
    worker_parser.add_argument("--idle-exit-seconds", type=float, default=None)

    args = parser.parse_args()

    if args.command == "templates":
        templates = list_job_templates()
        print(json.dumps({"templates": templates}, ensure_ascii=False, indent=2))
        return

    if args.command == "readiness":
        preset = resolve_routing_preset(args.preset) if args.preset else None
        providers = args.provider or (preset.providers if preset else None)
        print(json.dumps(build_readiness_report(
            perf_log_path=args.perf_log_file,
            provider_state_path=args.provider_state_file,
            providers=providers,
            latency_budget_seconds=preset.latency_budget_seconds if preset else None,
        ), ensure_ascii=False, indent=2))
        return

    if args.command == "control-report":
        preset = resolve_routing_preset(args.preset) if args.preset else None
        providers = args.provider or (preset.providers if preset else None)
        print(json.dumps(build_control_report(
            perf_log_path=args.perf_log_file,
            provider_state_path=args.provider_state_file,
            queue_dir=args.queue_dir,
            task_id=args.task_id,
            providers=providers,
            latency_budget_seconds=preset.latency_budget_seconds if preset else None,
            recent_batch_limit=args.recent_batch_limit,
        ), ensure_ascii=False, indent=2))
        return

    if args.command == "remediation-action":
        event = json.loads(args.event_json)
        proposal = propose_remediation_action(
            event,
            requested_action=args.action,
            approved=args.approved,
        )
        audit_path = append_remediation_audit(
            args.audit_log_file,
            proposal,
            actor=args.actor,
            evidence={"source": "router.main remediation-action"},
        )
        print(json.dumps({
            "ok": bool(proposal.get("allowed")),
            "proposal": proposal,
            "audit_path": audit_path,
        }, ensure_ascii=False, indent=2))
        return

    if args.command == "benchmark":
        preset = resolve_routing_preset(args.preset) if args.preset else None
        result = run_internal_benchmark(BenchmarkConfig(
            providers=args.provider or None,
            preset=preset,
            samples=args.samples,
            prompt=args.prompt,
            profile=args.profile,
            output_dir=args.output_dir,
            batch_dir=args.batch_dir,
            ledger_dir=args.ledger_dir,
            perf_log_file=args.perf_log_file,
            provider_state_file=args.provider_state_file,
        ))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    if args.command == "submit":
        preset = resolve_routing_preset(args.preset) if args.preset else None
        task = QueueStore(args.queue_dir).submit_task(
            args.job_file,
            preset=preset.name if preset else None,
            providers=args.provider or [],
            routing_policy=args.routing_policy,
            provider_tier=args.provider_tier,
            profile=args.profile,
            max_workers=args.max_workers,
            max_retries_per_provider=args.max_retries_per_provider,
            retry_delay_seconds=args.retry_delay_seconds,
            output_dir=args.output_dir,
            batch_dir=args.batch_dir,
            ledger_dir=args.ledger_dir,
            perf_log_file=args.perf_log_file,
            provider_state_file=args.provider_state_file,
            queue_priority=args.queue_priority,
            model_class=args.model_class,
            station_tier=args.station_tier,
            latency_budget_seconds=args.latency_budget_seconds,
        )
        print(json.dumps({"ok": True, "task_id": task["task_id"], "status": task["status"], "task": task}, ensure_ascii=False, indent=2))
        return

    if args.command == "status":
        task = QueueStore(args.queue_dir).task_status(args.task_id)
        print(json.dumps({"ok": task is not None, "task": task}, ensure_ascii=False, indent=2))
        return

    if args.command == "list-queue":
        rows = QueueStore(args.queue_dir).list_tasks(statuses=args.status or None)
        print(json.dumps({"ok": True, "tasks": rows}, ensure_ascii=False, indent=2))
        return

    if args.command == "cancel":
        task = QueueStore(args.queue_dir).cancel_task(args.task_id)
        print(json.dumps({"ok": task is not None, "task": task}, ensure_ascii=False, indent=2))
        return

    if args.command == "worker":
        worker = QueueWorker(QueueWorkerConfig(
            queue_dir=args.queue_dir,
            output_dir=args.output_dir,
            batch_dir=args.batch_dir,
            ledger_dir=args.ledger_dir,
            perf_log_path=args.perf_log_file,
            provider_state_path=args.provider_state_file,
            provider_lock_dir=args.provider_lock_dir,
            poll_interval_seconds=args.poll_interval_seconds,
            idle_exit_seconds=0 if args.once else args.idle_exit_seconds,
            worker_id=args.worker_id,
        ))
        if args.once:
            task = worker.run_once()
            print(json.dumps({"ok": task is not None, "task": task}, ensure_ascii=False, indent=2))
        else:
            tasks = worker.run_forever(max_tasks=args.max_tasks)
            print(json.dumps({"ok": True, "processed_count": len(tasks), "tasks": tasks}, ensure_ascii=False, indent=2))
        return

    if args.command in {"run", "explain-plan"}:
        preset = resolve_routing_preset(args.preset) if args.preset else None
        providers = args.provider or (preset.providers if preset and preset.providers else None)
        routing_policy = args.routing_policy or (preset.routing_policy if preset else None)
        provider_tier = args.provider_tier or (preset.provider_tier if preset else None)
        max_workers = (
            args.max_workers
            if args.max_workers is not None
            else (preset.max_workers if preset and preset.max_workers is not None else DEFAULT_CONFIG.max_workers)
        )
        max_retries_per_provider = (
            args.max_retries_per_provider
            if args.max_retries_per_provider is not None
            else (
                preset.max_retries_per_provider
                if preset and preset.max_retries_per_provider is not None
                else DEFAULT_CONFIG.max_retries_per_provider
            )
        )
        retry_delay_seconds = (
            args.retry_delay_seconds
            if args.retry_delay_seconds is not None
            else (
                preset.retry_delay_seconds
                if preset and preset.retry_delay_seconds is not None
                else DEFAULT_CONFIG.retry_delay_seconds
            )
        )
        dry_run = args.command == "explain-plan" or args.dry_run
        result = run_job_file(
            args.job_file,
            providers=providers,
            output_dir=args.output_dir,
            batch_dir=args.batch_dir,
            ledger_dir=args.ledger_dir,
            perf_log_path=args.perf_log_file,
            provider_state_path=args.provider_state_file,
            default_profile=args.profile,
            max_workers=(max_workers or None),
            max_retries_per_provider=max_retries_per_provider,
            retry_delay_seconds=retry_delay_seconds,
            routing_policy=routing_policy,
            provider_tier=provider_tier,
            preset_name=preset.name if preset else None,
            routing_preset=preset,
            dry_run=dry_run,
        )
        print(json.dumps({
            "ok": result.failure_count == 0,
            "dry_run": result.dry_run,
            "routing_preset": preset.name if preset else None,
            "job_type": result.job_type,
            "success_count": result.success_count,
            "failure_count": result.failure_count,
            "summary_path": result.summary_path,
            "ledger_path": result.ledger_path,
            "items": [
                {
                    "item_id": item.item_id,
                    "status": item.status,
                    "selected_provider": item.selected_provider,
                    "selected_provider_group": item.selected_provider_group,
                    "attempted_providers": item.attempted_providers,
                    "attempts": item.attempts,
                    "skipped_providers": item.skipped_providers,
                    "routing_policy": item.routing_policy,
                    "effective_routing_mode": item.effective_routing_mode,
                    "planned_providers": item.planned_providers,
                    "policy_warnings": item.policy_warnings,
                    "policy_plan": item.policy_plan,
                    "race_requested_providers": item.race_requested_providers,
                    "race_launched_providers": item.race_launched_providers,
                    "race_completed_providers": item.race_completed_providers,
                    "artifact_paths": item.artifact_paths,
                    "error_code": item.error_code,
                    "error_message": item.error_message,
                }
                for item in result.item_results
            ],
        }, ensure_ascii=False, indent=2))
        return


if __name__ == "__main__":
    main()
