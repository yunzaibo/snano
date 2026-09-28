from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from router.core.perf_score import (
    pool_correlation_signals,
    pool_scores,
    pool_status,
    pool_status_counts,
    provider_scores,
    provider_weight_recommendations,
    weighted_provider_slots,
)
from router.core.provider_registry import provider_startup_diagnostics
from router.core.source_config import ordered_lane_ids


def _read_events(paths: list[str]) -> list[dict]:
    events: list[dict] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            candidates = sorted(path.glob("*.jsonl"))
        else:
            candidates = [path]
        for candidate in candidates:
            if not candidate.exists():
                continue
            for line in candidate.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def _configured_candidates() -> tuple[list[str], list[dict]]:
    rows = provider_startup_diagnostics()
    eligible = {
        str(row.get("provider")): row
        for row in rows
        if bool(row.get("enabled", True)) and bool(row.get("key_present"))
    }
    ordered: list[str] = []
    for lane_id in ordered_lane_ids():
        if lane_id in eligible and lane_id not in ordered:
            ordered.append(lane_id)
    for row in rows:
        provider = str(row.get("provider") or "")
        if provider in eligible and provider not in ordered:
            ordered.append(provider)
    details = [
        {
            "provider": provider,
            "is_lane": bool(eligible[provider].get("is_lane")),
            "base_provider": eligible[provider].get("base_provider") or provider,
            "source_tier": eligible[provider].get("source_tier"),
            "provider_group": eligible[provider].get("provider_group"),
            "pool_identity": eligible[provider].get("pool_identity") or eligible[provider].get("provider_group"),
            "station_id": eligible[provider].get("station_id"),
            "account_id": eligible[provider].get("account_id"),
            "account_group": eligible[provider].get("account_group"),
            "credential_id": eligible[provider].get("credential_id"),
            "route_id": eligible[provider].get("route_id"),
            "model": eligible[provider].get("model"),
            "capability_bucket": eligible[provider].get("capability_bucket"),
            "max_concurrency": eligible[provider].get("max_concurrency"),
        }
        for provider in ordered
    ]
    return ordered, details


def _pool_rows(pool_score_rows: list[dict], configured_candidates: list[dict]) -> list[dict]:
    scores_by_identity = {str(row["pool_identity"]): row for row in pool_score_rows}
    configured_by_identity: dict[str, dict] = {}
    for candidate in configured_candidates:
        pool_identity = candidate.get("pool_identity")
        if not pool_identity:
            continue
        row = configured_by_identity.setdefault(str(pool_identity), {
            "pool_identity": str(pool_identity),
            "provider_group": candidate.get("provider_group"),
            "station_id": candidate.get("station_id"),
            "account_id": candidate.get("account_id"),
            "account_group": candidate.get("account_group"),
            "lanes": [],
        })
        row["lanes"].append(candidate.get("provider"))

    rows: list[dict] = []
    for pool_identity, configured in configured_by_identity.items():
        score = scores_by_identity.get(pool_identity)
        rows.append({
            **configured,
            "status": pool_status(score or {}),
            "attempt_count": int((score or {}).get("attempt_count") or 0),
            "success_count": int((score or {}).get("success_count") or 0),
            "success_rate": (score or {}).get("success_rate"),
            "p50_latency_ms": (score or {}).get("p50_latency_ms"),
            "p90_latency_ms": (score or {}).get("p90_latency_ms"),
            "avg_success_latency_ms": (score or {}).get("avg_success_latency_ms"),
            "errors": (score or {}).get("errors") or {},
            "artifact_count": int((score or {}).get("artifact_count") or 0),
            "valid_image_count": int((score or {}).get("valid_image_count") or 0),
            "invalid_image_count": int((score or {}).get("invalid_image_count") or 0),
            "aspect_ratio_mismatch_count": int((score or {}).get("aspect_ratio_mismatch_count") or 0),
            "window_seconds": (score or {}).get("window_seconds"),
            "window_event_count": int((score or {}).get("window_event_count") or 0),
            "score": (score or {}).get("score"),
            "providers": (score or {}).get("providers") or [],
        })
    for score in pool_score_rows:
        pool_identity = str(score["pool_identity"])
        if pool_identity in configured_by_identity:
            continue
        rows.append({
            **score,
            "provider_group": (score.get("provider_groups") or [None])[0],
            "station_id": None,
            "account_id": None,
            "account_group": None,
            "lanes": score.get("providers") or [],
            "status": pool_status(score),
        })
    return sorted(rows, key=lambda row: str(row["pool_identity"]))


def build_performance_report(paths: list[str], *, window_hours: float | None = None) -> dict:
    events = _read_events(paths)
    window_seconds = window_hours * 3600 if window_hours else None
    scores = provider_scores(events, window_seconds=window_seconds)
    pool_score_rows = pool_scores(events, window_seconds=window_seconds)
    configured_order, configured_candidates = _configured_candidates()
    pool_rows = _pool_rows(pool_score_rows, configured_candidates)
    provider_order = configured_order or None
    provider_pool_map = {
        str(row["provider"]): str(row["pool_identity"])
        for row in configured_candidates
        if row.get("provider") and row.get("pool_identity")
    }
    weight_rows = provider_weight_recommendations(
        scores,
        provider_order=provider_order,
        provider_pool_map=provider_pool_map,
        pools=pool_score_rows,
        include_extra_scores=True,
    )
    return {
        "event_count": len(events),
        "scoring_window_seconds": window_seconds,
        "configured_candidate_order": configured_order,
        "configured_candidates": configured_candidates,
        "provider_scores": scores,
        "pool_scores": pool_rows,
        "pool_status_counts": pool_status_counts(pool_rows),
        "recommended_t2i_order": [row["provider"] for row in scores if row["success_count"] > 0],
        "weighted_lane_recommendations": weight_rows,
        "weighted_lane_slots": weighted_provider_slots(
            scores,
            provider_order=provider_order,
            provider_pool_map=provider_pool_map,
            pools=pool_score_rows,
            include_extra_scores=True,
        ),
        "pool_correlation_signals": pool_correlation_signals(events),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize image router performance JSONL logs")
    parser.add_argument("paths", nargs="*", default=[str(PROJECT_ROOT / "logs")])
    parser.add_argument("--window-hours", type=float, default=None, help="Only score events recorded within this many recent hours")
    args = parser.parse_args()

    print(json.dumps(build_performance_report(args.paths, window_hours=args.window_hours), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
