from __future__ import annotations

import hashlib
import json
from pathlib import Path

from router.core.models import GenerationRequest


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def request_hash(request: GenerationRequest) -> str:
    payload = {
        "job_type": request.job_type,
        "request_type": request.request_type,
        "profile": request.profile,
        "prompt": request.prompt.strip(),
        "negative_prompt": request.negative_prompt.strip(),
        "reference_hashes": [_file_sha256(p) for p in request.reference_images],
        "count": request.count,
        "aspect_ratio": request.aspect_ratio,
        "size": request.size,
        "quality": request.quality,
        "metadata": request.metadata,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
