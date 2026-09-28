from __future__ import annotations

from typing import Any

from router.core.remediation_events import (
    remediation_event_from_attempt,
    remediation_events_from_items,
)


def _legacy_view(event: dict[str, Any] | None) -> dict[str, Any] | None:
    if event is None:
        return None
    row = dict(event)
    if row.get("error_category") == "rate_limit":
        row["action"] = "operator_check_quota"
        row["risk"] = "external_account"
        row["auto_execute"] = False
    return row


def recovery_event_from_attempt(attempt: dict[str, Any]) -> dict[str, Any] | None:
    return _legacy_view(remediation_event_from_attempt(attempt, source="attempt"))


def recovery_events_from_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in (_legacy_view(event) for event in remediation_events_from_items(items))
        if event is not None
    ]
