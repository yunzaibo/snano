from __future__ import annotations

import re
from typing import Any


REDACTION = "[REDACTED]"

_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "generation_id",
    "generationid",
    "job_id",
    "jobid",
    "poll_url",
    "pollurl",
    "request_id",
    "requestid",
    "secret",
    "status_url",
    "statusurl",
    "task_id",
    "taskid",
    "token",
)

_TEXT_PATTERNS = [
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)((?:api[_-]?key|token|secret|cookie)\s*[:=]\s*)['\"]?[^'\"\s,;]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\b[A-Za-z0-9._~+/=-]{64,}\b"),
]


def is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def redact_text(value: str | None) -> str | None:
    if value is None:
        return None
    redacted = value
    for pattern in _TEXT_PATTERNS:
        redacted = pattern.sub(lambda match: f"{match.group(1)}{REDACTION}" if match.lastindex else REDACTION, redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if is_sensitive_key(str(key)):
                redacted[key] = REDACTION
            else:
                redacted[key] = redact_value(item)
        return redacted
    return value
