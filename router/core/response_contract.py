from __future__ import annotations

from typing import Any


_STATUS_KEYS = {"status", "state", "phase"}
_PENDING_VALUES = {
    "accepted",
    "created",
    "in_progress",
    "pending",
    "processing",
    "queued",
    "running",
    "started",
    "submitted",
}
_TASK_ID_KEYS = {
    "generation_id",
    "generationId",
    "job_id",
    "jobId",
    "request_id",
    "requestId",
    "task_id",
    "taskId",
}
_POLL_URL_KEYS = {"poll_url", "pollUrl", "status_url", "statusUrl"}


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def response_contract_signal(provider_response: dict[str, Any] | None) -> dict[str, Any]:
    """Return a secret-safe summary of provider response semantics."""
    if not isinstance(provider_response, dict):
        return {}

    status_field: str | None = None
    status_value: str | None = None
    task_id_present = False
    poll_url_present = False

    for mapping in _walk(provider_response):
        for key, value in mapping.items():
            if key in _TASK_ID_KEYS and value:
                task_id_present = True
            if key in _POLL_URL_KEYS and value:
                poll_url_present = True
            if key in _STATUS_KEYS and isinstance(value, str):
                normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
                if normalized in _PENDING_VALUES and status_value is None:
                    status_field = key
                    status_value = normalized

    async_pending = bool(status_value and (task_id_present or poll_url_present or status_value in _PENDING_VALUES))
    if not async_pending and not task_id_present and not poll_url_present:
        return {}

    return {
        "async_pending": async_pending,
        "status_field": status_field,
        "status_value": status_value,
        "task_id_present": task_id_present,
        "poll_url_present": poll_url_present,
    }
