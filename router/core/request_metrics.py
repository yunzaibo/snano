from __future__ import annotations

from pathlib import Path
from typing import Any


def _estimated_base64_bytes(raw_bytes: int) -> int:
    return ((raw_bytes + 2) // 3) * 4


def reference_image_metrics(paths: list[str]) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    total_bytes = 0
    estimated_base64_bytes = 0
    missing_count = 0

    for raw_path in paths:
        path = Path(raw_path)
        exists = path.exists()
        size_bytes: int | None = None
        encoded_bytes: int | None = None
        if exists and path.is_file():
            size_bytes = path.stat().st_size
            encoded_bytes = _estimated_base64_bytes(size_bytes)
            total_bytes += size_bytes
            estimated_base64_bytes += encoded_bytes
        else:
            missing_count += 1

        files.append({
            "path": str(path),
            "exists": exists,
            "suffix": path.suffix.lower(),
            "bytes": size_bytes,
            "estimated_base64_bytes": encoded_bytes,
        })

    return {
        "count": len(paths),
        "total_bytes": total_bytes,
        "estimated_base64_bytes": estimated_base64_bytes,
        "missing_count": missing_count,
        "files": files,
    }
