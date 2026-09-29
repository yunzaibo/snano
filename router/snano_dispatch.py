from __future__ import annotations

from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
import re
from typing import Any, Sequence


DEFAULT_SNANO_PROVIDERS = [
    "apiyi-snano",
]

DEFAULT_SIMAGE_PROVIDERS = [
    "apiyi-simage-gpt-image-2-5-flare",
]

DEFAULT_SKILL_PROVIDERS = {
    "snano": DEFAULT_SNANO_PROVIDERS,
    "simage": DEFAULT_SIMAGE_PROVIDERS,
}

DEFAULT_SNANO_GROUP_SIZE = 5
DEFAULT_DIMENSION_PROFILES = {
    "defaults": {
        "size": "2K",
        "minLongEdge": 2048,
        "aspectRatio": "auto",
        "aspectStrict": False,
        "minShortEdgeMode": "soft",
    },
    "projectProfiles": {
        "amazoncar": {
            "aspectRatio": "auto",
            "aspectStrict": "when_aspect_ratio_present",
        },
    },
}

_CHINESE_IMAGE_COUNTS = {
    "一": 1,
    "两": 2,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def infer_image_count_from_prompt(prompt: str) -> int | None:
    """Infer an explicit image count without mistaking dimensions for counts."""
    text = str(prompt or "")
    arabic_patterns = (
        r"(?<![0-9x×])(\d{1,3})\s*(?:张|幅|个)\s*(?:图|图片)?",
        r"(?:generate|create|make|draw|render|produce)\s+(?:me\s+)?(\d{1,3})\s+(?:images?|pictures?|variants?)\b",
    )
    for pattern in arabic_patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))

    chinese_pattern = r"([一二两三四五六七八九十])\s*(?:张|幅|个)\s*(?:图|图片)?"
    match = re.search(chinese_pattern, text)
    if match:
        return _CHINESE_IMAGE_COUNTS[match.group(1)]
    return None


def normalize_image_size(value: str | None) -> str:
    """Accept quality tiers or explicit dimensions such as 2048x2048."""
    text = str(value or "2K").strip().upper().replace("×", "X")
    if text in {"1K", "2K", "4K"}:
        return text
    match = re.fullmatch(r"(\d{3,5})X(\d{3,5})", text)
    if match:
        width, height = (int(part) for part in match.groups())
        if width > 0 and height > 0:
            return f"{width}x{height}"
    raise ValueError("size must be 1K, 2K, 4K, or explicit dimensions such as 2048x2048.")


@dataclass(frozen=True, slots=True)
class SnanoProviderGroup:
    provider: str
    index: int
    items: list[dict[str, Any]]


def _provider_list(providers: Sequence[str] | None = None) -> list[str]:
    rows = list(providers or DEFAULT_SNANO_PROVIDERS)
    if not rows:
        raise ValueError("Snano dispatch requires at least one provider.")
    return rows


def _dimension_section(
    dimension_profiles: dict[str, Any] | None,
    name: str,
) -> dict[str, Any]:
    profiles = dimension_profiles or DEFAULT_DIMENSION_PROFILES
    value = profiles.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _project_dimension_profile(
    dimension_profiles: dict[str, Any] | None,
    project_profile: str | None,
) -> dict[str, Any]:
    if not project_profile:
        return {}
    profiles = dimension_profiles or DEFAULT_DIMENSION_PROFILES
    project_profiles = profiles.get("projectProfiles")
    if not isinstance(project_profiles, dict):
        return {}
    value = project_profiles.get(project_profile)
    return dict(value) if isinstance(value, dict) else {}


def _first_dimension_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _auto_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "auto":
        return None
    return text


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "strict"}


def _aspect_strict_value(value: Any, *, has_aspect_ratio: bool, default: bool = False) -> bool:
    raw = str(value or "").strip().lower()
    if raw in {"when_aspect_ratio_present", "if_aspect_ratio", "when_ratio_present"}:
        return has_aspect_ratio
    return _bool_value(value, default=default)


def _int_value(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def apply_dimension_defaults_to_items(
    items: Sequence[dict[str, Any]],
    *,
    project_profile: str | None = None,
    aspect_ratio: str | None = None,
    size: str | None = None,
    min_long_edge: int | None = None,
    aspect_strict: bool | None = None,
    min_short_edge: int | None = None,
    min_short_edge_mode: str | None = None,
    dimension_profiles: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    defaults = _dimension_section(dimension_profiles, "defaults")
    project = _project_dimension_profile(dimension_profiles, project_profile)
    resolved_size = _auto_to_none(_first_dimension_value(size, project.get("size"), defaults.get("size"), "2K"))
    resolved_aspect_ratio = _auto_to_none(_first_dimension_value(aspect_ratio, project.get("aspectRatio"), defaults.get("aspectRatio")))
    resolved_min_long_edge = _int_value(_first_dimension_value(min_long_edge, project.get("minLongEdge"), defaults.get("minLongEdge")), 2048)
    resolved_aspect_strict_policy = _first_dimension_value(aspect_strict, project.get("aspectStrict"), defaults.get("aspectStrict"))
    resolved_min_short_edge = _first_dimension_value(min_short_edge, project.get("minShortEdge"), defaults.get("minShortEdge"))
    resolved_min_short_edge_mode = str(
        _first_dimension_value(min_short_edge_mode, project.get("minShortEdgeMode"), defaults.get("minShortEdgeMode"), "soft")
    )

    normalized: list[dict[str, Any]] = []
    for item in items:
        row = dict(item)
        metadata = dict(row.get("metadata") or {})
        if row.get("size") in (None, "") and resolved_size:
            row["size"] = resolved_size
        if row.get("aspectRatio") in (None, "") and row.get("aspect_ratio") in (None, "") and resolved_aspect_ratio:
            row["aspectRatio"] = resolved_aspect_ratio
        row_aspect_ratio = _auto_to_none(_first_dimension_value(row.get("aspectRatio"), row.get("aspect_ratio")))
        resolved_aspect_strict = _aspect_strict_value(
            resolved_aspect_strict_policy,
            has_aspect_ratio=row_aspect_ratio is not None,
            default=False,
        )

        metadata.setdefault("artifactMinWidth", resolved_min_long_edge)
        metadata.setdefault("artifactMinHeight", resolved_min_long_edge)
        metadata.setdefault("artifactDimensionMode", "long_edge")
        metadata.setdefault("artifactAspectStrict", resolved_aspect_strict)
        metadata.setdefault("artifactMinShortEdgeMode", resolved_min_short_edge_mode)
        if resolved_min_short_edge not in (None, ""):
            metadata.setdefault("artifactMinShortEdge", _int_value(resolved_min_short_edge, 1))
        if project_profile:
            metadata.setdefault("projectProfile", project_profile)
        row["metadata"] = metadata
        normalized.append(row)
    return normalized


def default_items_from_prompt(
    *,
    prompt: str,
    reference: str | Path,
    count: int | None = None,
    per_provider: int | None = None,
    providers: Sequence[str] | None = None,
    project_profile: str | None = None,
    aspect_ratio: str | None = None,
    size: str | None = None,
    min_long_edge: int | None = None,
    aspect_strict: bool | None = None,
    min_short_edge: int | None = None,
    min_short_edge_mode: str | None = None,
    dimension_profiles: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    provider_rows = _provider_list(providers)
    if count is None:
        if per_provider is not None:
            if per_provider < 1:
                raise ValueError("per_provider must be >= 1.")
            count = per_provider * len(provider_rows)
        else:
            count = infer_image_count_from_prompt(prompt) or 1
    if count < 1:
        raise ValueError("count must be >= 1.")

    reference_path = str(Path(reference).expanduser())
    items = [
        {
            "id": f"snano-variant-{index:03d}",
            "requestType": "image_to_image",
            "prompt": prompt,
            "referenceImages": [reference_path],
            "quality": "high",
            "routingPolicy": "fallback",
            "metadata": {
                "source": "snano-skill",
                "variantIndex": index,
                "variantTotal": count,
                "requireFullReferenceLock": True,
            },
        }
        for index in range(1, count + 1)
    ]
    return apply_dimension_defaults_to_items(
        items,
        project_profile=project_profile,
        aspect_ratio=aspect_ratio,
        size=size,
        min_long_edge=min_long_edge,
        aspect_strict=aspect_strict,
        min_short_edge=min_short_edge,
        min_short_edge_mode=min_short_edge_mode,
        dimension_profiles=dimension_profiles,
    )


def default_skill_items_from_prompt(
    *,
    skill: str,
    prompt: str,
    references: Sequence[str | Path] | None = None,
    count: int | None = None,
    per_provider: int | None = None,
    providers: Sequence[str] | None = None,
    project_profile: str | None = None,
    aspect_ratio: str | None = None,
    size: str | None = None,
    min_long_edge: int | None = None,
    aspect_strict: bool | None = None,
    min_short_edge: int | None = None,
    min_short_edge_mode: str | None = None,
    dimension_profiles: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    skill_name = skill.strip().lower()
    if skill_name not in DEFAULT_SKILL_PROVIDERS:
        raise ValueError("skill must be 'snano' or 'simage'.")

    provider_rows = _provider_list(providers or DEFAULT_SKILL_PROVIDERS[skill_name])
    if count is None:
        if per_provider is not None:
            if per_provider < 1:
                raise ValueError("per_provider must be >= 1.")
            count = per_provider * len(provider_rows)
        else:
            count = infer_image_count_from_prompt(prompt) or 1
    if count < 1:
        raise ValueError("count must be >= 1.")

    reference_paths = [str(Path(ref).expanduser()) for ref in (references or [])]
    request_type = "image_to_image" if reference_paths else "text_to_image"
    items = []
    for index in range(1, count + 1):
        metadata = {
            "source": f"{skill_name}-skill",
            "variantIndex": index,
            "variantTotal": count,
        }
        if reference_paths:
            metadata["requireFullReferenceLock"] = True
        item = {
            "id": f"{skill_name}-variant-{index:03d}",
            "requestType": request_type,
            "prompt": prompt,
            "referenceImages": reference_paths,
            "quality": "high",
            "routingPolicy": "fallback",
            "metadata": metadata,
        }
        items.append(item)

    return apply_dimension_defaults_to_items(
        items,
        project_profile=project_profile,
        aspect_ratio=aspect_ratio,
        size=size,
        min_long_edge=min_long_edge,
        aspect_strict=aspect_strict,
        min_short_edge=min_short_edge,
        min_short_edge_mode=min_short_edge_mode,
        dimension_profiles=dimension_profiles,
    )


def assign_items_evenly(
    items: Sequence[dict[str, Any]],
    providers: Sequence[str] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    provider_rows = _provider_list(providers)
    assigned = {provider: [] for provider in provider_rows}
    for index, item in enumerate(items):
        assigned[provider_rows[index % len(provider_rows)]].append(dict(item))
    return assigned


def chunk_provider_items(
    assigned: dict[str, Sequence[dict[str, Any]]],
    *,
    group_size: int = DEFAULT_SNANO_GROUP_SIZE,
) -> dict[str, list[list[dict[str, Any]]]]:
    if group_size < 1:
        raise ValueError("group_size must be >= 1.")
    return {
        provider: [
            [dict(item) for item in rows[index : index + group_size]]
            for index in range(0, len(rows), group_size)
        ]
        for provider, rows in assigned.items()
    }


def build_execution_rounds(
    items: Sequence[dict[str, Any]],
    providers: Sequence[str] | None = None,
    *,
    group_size: int = DEFAULT_SNANO_GROUP_SIZE,
) -> list[list[SnanoProviderGroup]]:
    provider_rows = _provider_list(providers)
    assigned = assign_items_evenly(items, provider_rows)
    chunks = chunk_provider_items(assigned, group_size=group_size)

    rounds: list[list[SnanoProviderGroup]] = []
    chunk_lists = [chunks[provider] for provider in provider_rows]
    for round_chunks in zip_longest(*chunk_lists):
        execution_round: list[SnanoProviderGroup] = []
        for provider, provider_chunk in zip(provider_rows, round_chunks):
            if provider_chunk:
                execution_round.append(
                    SnanoProviderGroup(
                        provider=provider,
                        index=len(rounds) + 1,
                        items=provider_chunk,
                    )
                )
        if execution_round:
            rounds.append(execution_round)
    return rounds


def max_workers_for_batch(
    items: Sequence[dict[str, Any]],
    *,
    requested_workers: int | None = None,
) -> int:
    if requested_workers is not None and requested_workers > 0:
        return requested_workers
    return max(len(items), 1)


def load_manifest_items(path: str | Path) -> tuple[str, list[dict[str, Any]]]:
    import json

    manifest_path = Path(path).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("jobType") != "manifest":
        raise ValueError("Snano manifest input must use jobType=manifest.")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Snano manifest input must contain a non-empty items array.")
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each Snano manifest item must be an object.")
        normalized.append(dict(item))
    return str(manifest_path), normalized
