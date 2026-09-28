from __future__ import annotations

import struct
from pathlib import Path
from typing import Any


def parse_aspect_ratio(value: str | None) -> float | None:
    if not value:
        return None
    raw = value.strip().lower()
    if not raw:
        return None
    if ":" in raw:
        left, right = raw.split(":", 1)
    elif "x" in raw:
        left, right = raw.split("x", 1)
    else:
        return None
    try:
        width = float(left.strip())
        height = float(right.strip())
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width / height


def _png_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 24 or not blob.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    return struct.unpack(">II", blob[16:24])


def _gif_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 10 or not (blob.startswith(b"GIF87a") or blob.startswith(b"GIF89a")):
        return None
    return struct.unpack("<HH", blob[6:10])


def _jpeg_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 4 or not blob.startswith(b"\xff\xd8"):
        return None
    idx = 2
    while idx + 9 < len(blob):
        if blob[idx] != 0xFF:
            idx += 1
            continue
        marker = blob[idx + 1]
        idx += 2
        if marker in {0xD8, 0xD9}:
            continue
        if idx + 2 > len(blob):
            return None
        segment_len = int.from_bytes(blob[idx:idx + 2], "big")
        if segment_len < 2 or idx + segment_len > len(blob):
            return None
        if marker in {
            0xC0,
            0xC1,
            0xC2,
            0xC3,
            0xC5,
            0xC6,
            0xC7,
            0xC9,
            0xCA,
            0xCB,
            0xCD,
            0xCE,
            0xCF,
        }:
            if segment_len < 7:
                return None
            height = int.from_bytes(blob[idx + 3:idx + 5], "big")
            width = int.from_bytes(blob[idx + 5:idx + 7], "big")
            return width, height
        idx += segment_len
    return None


def _webp_size(blob: bytes) -> tuple[int, int] | None:
    if len(blob) < 30 or not (blob.startswith(b"RIFF") and blob[8:12] == b"WEBP"):
        return None
    chunk = blob[12:16]
    if chunk == b"VP8X" and len(blob) >= 30:
        width = 1 + int.from_bytes(blob[24:27], "little")
        height = 1 + int.from_bytes(blob[27:30], "little")
        return width, height
    if chunk == b"VP8L" and len(blob) >= 25:
        bits = int.from_bytes(blob[21:25], "little")
        width = 1 + (bits & 0x3FFF)
        height = 1 + ((bits >> 14) & 0x3FFF)
        return width, height
    return None


def inspect_image_blob(blob: bytes) -> dict[str, Any]:
    detectors = (
        ("png", _png_size),
        ("jpeg", _jpeg_size),
        ("gif", _gif_size),
        ("webp", _webp_size),
    )
    for image_format, detector in detectors:
        size = detector(blob)
        if size is not None:
            width, height = size
            return {
                "valid_image": width > 0 and height > 0,
                "format": image_format,
                "width": width,
                "height": height,
                "bytes": len(blob),
            }
    return {
        "valid_image": False,
        "format": "unknown",
        "width": None,
        "height": None,
        "bytes": len(blob),
    }


def artifact_metrics(
    paths: list[str],
    *,
    expected_aspect_ratio: str | None = None,
    aspect_ratio_tolerance: float = 0.03,
) -> dict[str, Any]:
    images: list[dict[str, Any]] = []
    expected_ratio = parse_aspect_ratio(expected_aspect_ratio)
    for path in paths:
        try:
            blob = Path(path).read_bytes()
            row = inspect_image_blob(blob)
            row["path"] = path
        except OSError as exc:
            row = {
                "path": path,
                "valid_image": False,
                "format": "missing",
                "width": None,
                "height": None,
                "bytes": 0,
                "error": type(exc).__name__,
            }
        width = row.get("width")
        height = row.get("height")
        actual_ratio = (
            round(float(width) / float(height), 6)
            if isinstance(width, int) and isinstance(height, int) and height > 0
            else None
        )
        row["aspect_ratio"] = actual_ratio
        if expected_ratio is not None and actual_ratio is not None:
            delta_pct = abs(actual_ratio - expected_ratio) / expected_ratio
            row["aspect_ratio_delta_pct"] = round(delta_pct, 6)
            row["aspect_ratio_matches"] = delta_pct <= aspect_ratio_tolerance
        elif expected_ratio is not None:
            row["aspect_ratio_delta_pct"] = None
            row["aspect_ratio_matches"] = False
        images.append(row)

    widths = [row["width"] for row in images if isinstance(row.get("width"), int)]
    heights = [row["height"] for row in images if isinstance(row.get("height"), int)]
    formats = sorted({str(row["format"]) for row in images})
    valid_count = sum(1 for row in images if row.get("valid_image"))
    ratio_checked = [row for row in images if expected_ratio is not None and row.get("valid_image")]
    ratio_mismatches = [
        row for row in ratio_checked
        if row.get("aspect_ratio_matches") is not True
    ]
    strict_ratio_checked = expected_ratio is not None
    return {
        "artifact_count": len(images),
        "valid_image_count": valid_count,
        "invalid_image_count": len(images) - valid_count,
        "total_bytes": sum(int(row.get("bytes") or 0) for row in images),
        "formats": formats,
        "min_width": min(widths) if widths else None,
        "max_width": max(widths) if widths else None,
        "min_height": min(heights) if heights else None,
        "max_height": max(heights) if heights else None,
        "expected_aspect_ratio": expected_aspect_ratio,
        "expected_aspect_ratio_value": round(expected_ratio, 6) if expected_ratio is not None else None,
        "aspect_ratio_checked_count": len(ratio_checked),
        "aspect_ratio_mismatch_count": len(ratio_mismatches),
        "aspect_ratio_tolerance": aspect_ratio_tolerance if expected_ratio is not None else None,
        "strict_aspect_ratio_checked": strict_ratio_checked,
        "strict_aspect_ratio_check_reason": (
            "checked"
            if strict_ratio_checked
            else "expected_aspect_ratio_missing"
        ),
        "images": images,
    }
