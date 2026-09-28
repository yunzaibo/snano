from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class LaneConfig:
    lane_id: str
    provider: str
    provider_group: str
    api_key_env: str
    station_id: str | None = None
    account_id: str | None = None
    account_group: str | None = None
    credential_id: str | None = None
    route_id: str | None = None
    pool_identity: str | None = None
    source_tier: str = "daily"
    base_url: str | None = None
    route_path: str | None = None
    model: str | None = None
    model_class: str | None = None
    default_size: str | None = None
    degradation_status: str | None = None
    timeout_env: str | None = None
    timeout_seconds: int | None = None
    preflight_timeout_seconds: float | None = None
    capability_bucket: str | None = None
    max_reference_images: int | None = None
    max_concurrency: int = 1
    async_remote: bool = False
    enabled: bool = True
    priority: int = 100
    image_contract: dict[str, Any] = field(default_factory=dict)
    extra_header_envs: dict[str, str] = field(default_factory=dict)
    extra_headers: dict[str, str] = field(default_factory=dict)


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


def _source_config_paths() -> list[Path]:
    raw = os.environ.get("MIR_SOURCES_CONFIG_FILE")
    if raw:
        return [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]

    paths = [PROJECT_ROOT / "configs" / "sources.yaml"]
    local_dir = PROJECT_ROOT / "configs" / "local"
    if local_dir.exists():
        paths.extend(sorted(local_dir.glob("*.yaml")))
        paths.extend(sorted(local_dir.glob("*.yml")))
    return paths


def _merge_sources(paths: list[Path]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in paths:
        data = _read_yaml(path)
        for section in ("stations", "accounts", "credentials", "routes", "lanes", "routingPolicies"):
            if isinstance(data.get(section), dict):
                merged.setdefault(section, {}).update(data[section])
    return merged


def _secret_env_name(secret_ref: dict[str, Any] | None) -> str | None:
    if not isinstance(secret_ref, dict):
        return None
    if secret_ref.get("type") != "env":
        return None
    name = secret_ref.get("name")
    return str(name) if name else None


def _header_envs(*sources: dict[str, Any] | None) -> tuple[dict[str, str], dict[str, str]]:
    envs: dict[str, str] = {}
    literals: dict[str, str] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        headers = source.get("headers") or source.get("extraHeaders") or {}
        if not isinstance(headers, dict):
            continue
        for header_name, value in headers.items():
            if isinstance(value, dict):
                env_name = _secret_env_name(value)
                if env_name:
                    envs[str(header_name)] = env_name
                elif value.get("value"):
                    literals[str(header_name)] = str(value["value"])
            elif isinstance(value, str):
                literals[str(header_name)] = value
    return envs, literals


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def load_lane_configs() -> dict[str, LaneConfig]:
    data = _merge_sources(_source_config_paths())
    stations = data.get("stations") or {}
    accounts = data.get("accounts") or {}
    credentials = data.get("credentials") or {}
    routes = data.get("routes") or {}
    lanes = data.get("lanes") or {}
    if not isinstance(lanes, dict):
        return {}

    parsed: dict[str, LaneConfig] = {}
    for lane_id, lane in lanes.items():
        if not isinstance(lane, dict):
            continue
        provider = str(lane.get("provider") or "").strip().lower()
        credential_id = lane.get("credential")
        route_id = lane.get("route")
        station_id = lane.get("station")
        credential = credentials.get(credential_id, {}) if isinstance(credentials, dict) else {}
        route = routes.get(route_id, {}) if isinstance(routes, dict) else {}
        station_key = station_id or (route.get("station") if isinstance(route, dict) else None)
        station = stations.get(station_key, {}) if isinstance(stations, dict) else {}
        account_id = lane.get("account") or (credential.get("account") if isinstance(credential, dict) else None)
        account = accounts.get(account_id, {}) if isinstance(accounts, dict) else {}
        if not provider or not isinstance(credential, dict):
            continue
        api_key_env = _secret_env_name(credential.get("secretRef"))
        if not api_key_env:
            continue

        header_envs, literal_headers = _header_envs(station, account, credential, route, lane)
        provider_group = (
            lane.get("providerGroup")
            or lane.get("provider_group")
            or station.get("vendorGroup")
            or station.get("providerGroup")
            or provider
        )
        source_tier = (
            lane.get("sourceTier")
            or lane.get("source_tier")
            or lane.get("tier")
            or station.get("sourceTier")
            or station.get("source_tier")
            or station.get("tier")
            or "daily"
        )
        base_url = lane.get("baseUrl") or route.get("baseUrl") or station.get("baseUrl")
        route_path = lane.get("path") or lane.get("routePath") or route.get("path")
        timeout_seconds = lane.get("timeoutSeconds") or lane.get("timeout_seconds")
        preflight_timeout_seconds = (
            lane.get("preflightTimeoutSeconds")
            or lane.get("preflight_timeout_seconds")
            or lane.get("connectProbeTimeoutSeconds")
            or lane.get("connect_probe_timeout_seconds")
        )
        account_group = _first_text(
            lane.get("accountGroup"),
            lane.get("account_group"),
            lane.get("poolGroup"),
            lane.get("pool_group"),
            account.get("accountGroup") if isinstance(account, dict) else None,
            account.get("account_group") if isinstance(account, dict) else None,
            account.get("poolGroup") if isinstance(account, dict) else None,
            account.get("pool_group") if isinstance(account, dict) else None,
            credential.get("accountGroup") if isinstance(credential, dict) else None,
            credential.get("account_group") if isinstance(credential, dict) else None,
            credential.get("poolGroup") if isinstance(credential, dict) else None,
            credential.get("pool_group") if isinstance(credential, dict) else None,
        )
        pool_identity = _first_text(
            lane.get("poolIdentity"),
            lane.get("pool_identity"),
            lane.get("quotaPool"),
            lane.get("quota_pool"),
            route.get("poolIdentity") if isinstance(route, dict) else None,
            route.get("pool_identity") if isinstance(route, dict) else None,
            credential.get("poolIdentity") if isinstance(credential, dict) else None,
            credential.get("pool_identity") if isinstance(credential, dict) else None,
            account.get("poolIdentity") if isinstance(account, dict) else None,
            account.get("pool_identity") if isinstance(account, dict) else None,
        )
        parsed[str(lane_id).strip().lower()] = LaneConfig(
            lane_id=str(lane_id).strip().lower(),
            provider=provider,
            provider_group=str(provider_group),
            api_key_env=api_key_env,
            station_id=str(station_key) if station_key else None,
            account_id=str(account_id) if account_id else None,
            account_group=account_group,
            credential_id=str(credential_id) if credential_id else None,
            route_id=str(route_id) if route_id else None,
            pool_identity=pool_identity,
            source_tier=str(source_tier).strip().lower(),
            base_url=str(base_url).rstrip("/") if base_url else None,
            route_path=str(route_path) if route_path else None,
            model=str(lane["model"]) if lane.get("model") else None,
            model_class=str(lane.get("modelClass") or lane.get("model_class")) if (lane.get("modelClass") or lane.get("model_class")) else None,
            default_size=str(lane.get("defaultSize") or lane.get("default_size")) if (lane.get("defaultSize") or lane.get("default_size")) else None,
            degradation_status=str(lane.get("degradationStatus") or lane.get("degradation_status")) if (lane.get("degradationStatus") or lane.get("degradation_status")) else None,
            timeout_env=str(lane["timeoutEnv"]) if lane.get("timeoutEnv") else None,
            timeout_seconds=int(timeout_seconds) if timeout_seconds else None,
            preflight_timeout_seconds=(
                float(preflight_timeout_seconds)
                if preflight_timeout_seconds
                else None
            ),
            capability_bucket=str(lane.get("capabilityBucket") or lane.get("capability_bucket") or "") or None,
            max_reference_images=(
                int(lane["maxReferenceImages"])
                if lane.get("maxReferenceImages") is not None
                else None
            ),
            max_concurrency=int(lane.get("maxConcurrency") or lane.get("max_concurrency") or 1),
            async_remote=bool(lane.get("asyncRemote") or lane.get("async_remote") or False),
            enabled=bool(lane.get("enabled", True)),
            priority=int(lane.get("priority") or 100),
            image_contract=dict(lane.get("imageContract") or lane.get("image_contract") or {}),
            extra_header_envs=header_envs,
            extra_headers=literal_headers,
        )
    return parsed


def ordered_lane_ids() -> list[str]:
    lanes = load_lane_configs()
    return [
        lane.lane_id
        for lane in sorted(lanes.values(), key=lambda row: (row.priority, row.lane_id))
        if lane.enabled and lane.degradation_status != "degraded_excluded"
    ]
