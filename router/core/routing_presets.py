from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class RoutingPreset:
    name: str
    providers: list[str] = field(default_factory=list)
    routing_policy: str | None = None
    provider_tier: str | None = None
    max_retries_per_provider: int | None = None
    retry_delay_seconds: float | None = None
    max_workers: int | None = None
    spring_back_seconds: int | None = None
    spring_back_probe_every: int | None = None
    latency_budget_seconds: int | None = None
    default_size: str | None = None
    artifact_min_width: int | None = None
    artifact_min_height: int | None = None
    artifact_dimension_mode: str | None = None
    notes: list[str] = field(default_factory=list)
    model_selection: str | None = None


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _preset_paths() -> list[Path]:
    raw = os.environ.get("MIR_ROUTING_PRESETS_FILE")
    if raw:
        return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]

    paths = [PROJECT_ROOT / "configs" / "routing-presets.yaml"]
    local_dir = PROJECT_ROOT / "configs" / "local"
    if local_dir.exists():
        paths.extend(sorted(local_dir.glob("routing-presets*.yaml")))
        paths.extend(sorted(local_dir.glob("routing-presets*.yml")))
    return paths


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_routing_presets() -> dict[str, RoutingPreset]:
    raw_presets: dict[str, Any] = {}
    for path in _preset_paths():
        data = _read_yaml(path)
        section = data.get("presets") if isinstance(data, dict) else None
        if isinstance(section, dict):
            raw_presets.update(section)

    presets: dict[str, RoutingPreset] = {}
    for name, row in raw_presets.items():
        if not isinstance(row, dict):
            continue
        preset_name = str(name).strip()
        if not preset_name:
            continue
        providers = _string_list(row.get("providers") or row.get("providerPreference"))
        routing_policy = _first_text(row.get("routingPolicy"), row.get("routing_policy"), row.get("policy"))
        provider_tier = _first_text(
            row.get("providerTier"),
            row.get("provider_tier"),
            row.get("sourceTier"),
            row.get("source_tier"),
        )
        presets[preset_name.lower()] = RoutingPreset(
            name=preset_name,
            providers=providers,
            routing_policy=routing_policy,
            provider_tier=provider_tier,
            max_retries_per_provider=_int_or_none(
                row.get("maxRetriesPerProvider") or row.get("max_retries_per_provider")
            ),
            retry_delay_seconds=_float_or_none(row.get("retryDelaySeconds") or row.get("retry_delay_seconds")),
            max_workers=_int_or_none(row.get("maxWorkers") or row.get("max_workers")),
            spring_back_seconds=_int_or_none(row.get("springBackSeconds") or row.get("spring_back_seconds")),
            spring_back_probe_every=_int_or_none(row.get("springBackProbeEvery") or row.get("spring_back_probe_every")),
            latency_budget_seconds=_int_or_none(row.get("latencyBudgetSeconds") or row.get("latency_budget_seconds")),
            default_size=_first_text(row.get("defaultSize"), row.get("default_size"), row.get("requestSize"), row.get("request_size")),
            artifact_min_width=_int_or_none(row.get("artifactMinWidth") or row.get("artifact_min_width")),
            artifact_min_height=_int_or_none(row.get("artifactMinHeight") or row.get("artifact_min_height")),
            artifact_dimension_mode=_first_text(row.get("artifactDimensionMode"), row.get("artifact_dimension_mode")),
            notes=_string_list(row.get("notes")),
            model_selection=_first_text(row.get("modelSelection")),
        )
    return presets


def resolve_routing_preset(name: str | None) -> RoutingPreset | None:
    if not name:
        return None
    preset = load_routing_presets().get(name.strip().lower())
    if not preset:
        available = ", ".join(sorted(p.name for p in load_routing_presets().values())) or "<none>"
        raise ValueError(f"Unknown routing preset {name!r}. Available presets: {available}")
    return preset
