from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from router.core.artifact_metrics import artifact_metrics, parse_aspect_ratio
from router.core.dedupe import request_hash
from router.core.models import GenerationRequest, GenerationResult
from router.core.perf_log import append_generation_result
from router.core.provider_registry import provider_pool_metadata_for
from router.core.redaction import redact_text, redact_value
from router.core.request_metrics import reference_image_metrics
from router.core.response_contract import response_contract_signal


def _int_metadata_value(metadata: dict[str, Any], *names: str) -> int | None:
    for name in names:
        value = metadata.get(name)
        if value is None or value == "":
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _dimension_contract_mode(metadata: dict[str, Any]) -> str:
    raw = str(metadata.get("artifactDimensionMode") or metadata.get("artifact_dimension_mode") or "long_edge")
    normalized = raw.strip().lower().replace("-", "_")
    if normalized in {"both_edges", "min_width_height", "strict_width_height"}:
        return "both_edges"
    return "long_edge"


def _bool_metadata_value(metadata: dict[str, Any], *names: str, default: bool = False) -> bool:
    for name in names:
        value = metadata.get(name)
        if value is None or value == "":
            continue
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "strict"}
    return default


def _short_edge_floor(long_edge: int, expected_ratio: float | None) -> int:
    if expected_ratio is None or expected_ratio <= 0:
        return max(1, int(long_edge * 0.5))
    if expected_ratio >= 1:
        return max(1, int(round(long_edge / expected_ratio)))
    return max(1, int(round(long_edge * expected_ratio)))


def _apply_min_dimension_contract(result: GenerationResult, request_meta: dict[str, Any]) -> None:
    min_width = _int_metadata_value(request_meta, "artifactMinWidth", "artifact_min_width")
    min_height = _int_metadata_value(request_meta, "artifactMinHeight", "artifact_min_height")
    if min_width is None and min_height is None:
        return

    metrics = result.artifact_metrics or {}
    images = metrics.get("images") if isinstance(metrics, dict) else None
    if not isinstance(images, list):
        return

    mode = _dimension_contract_mode(request_meta)
    long_edge_min = max(value for value in (min_width, min_height) if value is not None)
    expected_ratio = parse_aspect_ratio(str(metrics.get("expected_aspect_ratio") or "") or None)
    aspect_strict = _bool_metadata_value(request_meta, "artifactAspectStrict", "artifact_aspect_strict", default=False)
    short_edge_min = _int_metadata_value(request_meta, "artifactMinShortEdge", "artifact_min_short_edge")
    if short_edge_min is None:
        short_edge_min = _short_edge_floor(long_edge_min, expected_ratio)

    mismatches: list[dict[str, Any]] = []
    for image in images:
        if not isinstance(image, dict) or not image.get("valid_image"):
            continue
        width = image.get("width")
        height = image.get("height")
        if mode == "both_edges":
            width_ok = min_width is None or (isinstance(width, int) and width >= min_width)
            height_ok = min_height is None or (isinstance(height, int) and height >= min_height)
            matches = bool(width_ok and height_ok)
        else:
            long_edge = max(width, height) if isinstance(width, int) and isinstance(height, int) else 0
            short_edge = min(width, height) if isinstance(width, int) and isinstance(height, int) else 0
            long_ok = long_edge >= long_edge_min
            short_ok = short_edge >= short_edge_min
            ratio_ok = image.get("aspect_ratio_matches") is not False
            matches = bool(long_ok and (ratio_ok or not aspect_strict))
            image["long_edge"] = long_edge
            image["short_edge"] = short_edge
            image["long_edge_min"] = long_edge_min
            image["short_edge_min"] = short_edge_min
            image["long_edge_matches"] = long_ok
            image["short_edge_matches"] = short_ok
            image["soft_short_edge_matches"] = short_ok
            image["soft_aspect_ratio_matches"] = ratio_ok
            image["aspect_ratio_strict"] = aspect_strict
            image["human_4k_matches"] = matches
        image["min_dimension_matches"] = matches
        if not image["min_dimension_matches"]:
            mismatches.append(image)

    metrics["expected_min_width"] = min_width
    metrics["expected_min_height"] = min_height
    metrics["dimension_contract_mode"] = mode
    metrics["expected_min_long_edge"] = long_edge_min if mode == "long_edge" else None
    metrics["expected_min_short_edge"] = short_edge_min if mode == "long_edge" else None
    metrics["aspect_ratio_strict"] = aspect_strict if mode == "long_edge" else None
    metrics["dimension_checked_count"] = sum(
        1 for image in images if isinstance(image, dict) and image.get("valid_image")
    )
    metrics["dimension_mismatch_count"] = len(mismatches)
    result.artifact_metrics = metrics
    if mismatches and result.ok:
        first = mismatches[0]
        min_text = (
            f"long edge >= {long_edge_min}"
            + (", aspect ratio near target" if aspect_strict else "")
            if mode == "long_edge"
            else f"{min_width or '*'}x{min_height or '*'}"
        )
        actual_text = f"{first.get('width')}x{first.get('height')}"
        result.ok = False
        result.error_code = "invalid_artifact"
        result.error_message = f"Artifact dimensions {actual_text} below required 4K contract ({min_text})."


def write_result(
    base_dir: str,
    provider: str,
    request: GenerationRequest,
    result: GenerationResult,
    perf_log_path: str | None = None,
) -> GenerationResult:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    req_hash = request_hash(request)
    out_dir = Path(base_dir) / provider / ts / req_hash[:12]
    out_dir.mkdir(parents=True, exist_ok=True)

    artifact_paths: list[str] = []
    for idx, blob in enumerate(result.artifact_blobs, start=1):
        img_path = out_dir / f"image-{idx:02d}.png"
        img_path.write_bytes(blob)
        artifact_paths.append(str(img_path))
    result.artifact_metrics = artifact_metrics(
        artifact_paths,
        expected_aspect_ratio=request.aspect_ratio,
    )

    request_meta: dict[str, Any] = redact_value(request.metadata) if isinstance(request.metadata, dict) else {}
    _apply_min_dimension_contract(result, request_meta)

    raw_path = out_dir / "raw-response.json"
    if result.provider_response is not None:
        result.provider_response = redact_value(result.provider_response)
        raw_path.write_text(json.dumps(result.provider_response, ensure_ascii=False, indent=2), encoding="utf-8")

    result.error_message = redact_text(result.error_message)
    ref_metrics = request_meta.get("referenceMetrics") or reference_image_metrics(request.reference_images)
    review_name_parts = [
        str(request_meta.get("autoAmazonItemId") or request_meta.get("itemId") or req_hash[:12]),
        str(request_meta.get("outputType") or request_meta.get("sceneFamilyKey") or "image"),
        result.provider,
    ]
    if request.aspect_ratio:
        review_name_parts.append(request.aspect_ratio.replace(":", "x"))
    if request.size:
        review_name_parts.append(str(request.size))
    review_basename = "__".join(part.strip().replace("/", "-") for part in review_name_parts if part)
    pool_metadata = provider_pool_metadata_for(result.provider)

    meta = {
        "provider": result.provider,
        **pool_metadata,
        "model": result.model,
        "request_type": request.request_type,
        "reference_count": len(request.reference_images),
        "reference_metrics": ref_metrics,
        "latency_ms": result.latency_ms,
        "timings_ms": result.timings_ms,
        "request_hash": req_hash,
        "artifacts": artifact_paths,
        "artifact_metrics": result.artifact_metrics,
        "error_code": result.error_code,
        "error_message": result.error_message,
        "provider_response_contract": response_contract_signal(result.provider_response),
        "request_metadata": request_meta,
        "aspect_ratio": request.aspect_ratio,
        "size": request.size,
        "review_basename": review_basename,
        "timestamp": ts,
    }
    (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    result.artifact_paths = artifact_paths
    result.raw_response_path = str(raw_path) if result.provider_response is not None else None
    append_generation_result(request, result, perf_log_path=perf_log_path)
    return result
