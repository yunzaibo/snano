from __future__ import annotations

import time
from typing import Any

from router.core.redaction import redact_value


SAFE_ACTION_POLICY_SCHEMA_VERSION = "safe-action-policy/v1"


_ACTION_POLICIES = (
    (("recharge", "billing", "balance", "quota", "spend", "付款", "充值", "余额"), {
        "safety_class": "external_spend",
        "approval_required": True,
        "auto_execute": False,
    }),
    (("key", "credential", "secret", "token", "rotate", "密钥"), {
        "safety_class": "secret_or_account",
        "approval_required": True,
        "auto_execute": False,
    }),
    (("account", "panel", "risk", "风控", "账户", "账号"), {
        "safety_class": "external_account",
        "approval_required": True,
        "auto_execute": False,
    }),
    (("model", "route", "disable", "enable", "config", "配置"), {
        "safety_class": "local_config",
        "approval_required": True,
        "auto_execute": False,
    }),
    (("cooldown", "retry", "fallback", "diagnostic", "collect", "evidence"), {
        "safety_class": "local_safe",
        "approval_required": False,
        "auto_execute": True,
    }),
)


def classify_action(action: str) -> dict[str, Any]:
    normalized = str(action or "").strip().lower()
    for markers, policy in _ACTION_POLICIES:
        if any(marker in normalized for marker in markers):
            return dict(policy)
    return {
        "safety_class": "local_safe",
        "approval_required": False,
        "auto_execute": True,
    }


def propose_remediation_action(
    event: dict[str, Any],
    *,
    requested_action: str | None = None,
    approved: bool = False,
) -> dict[str, Any]:
    action = requested_action or str(event.get("recommended_action") or event.get("action") or "collect_evidence_retry_or_fallback")
    action_policy = classify_action(action)
    event_requires_approval = bool(event.get("approval_required"))
    approval_required = event_requires_approval or bool(action_policy["approval_required"])
    allowed = not approval_required or approved
    status = "allowed" if allowed else "blocked_requires_approval"
    proposal = redact_value({
        "schema_version": SAFE_ACTION_POLICY_SCHEMA_VERSION,
        "created_at": time.time(),
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "provider": event.get("provider"),
        "action": action,
        "safety_class": action_policy["safety_class"],
        "event_safety_class": event.get("safety_class"),
        "approval_required": approval_required,
        "approved": bool(approved),
        "auto_execute": bool(action_policy["auto_execute"]) and not approval_required,
        "allowed": allowed,
        "status": status,
        "policy_decision": (
            "local_safe_action"
            if allowed and not approval_required
            else ("approved_manual_action" if allowed else "requires_human_approval")
        ),
        "secret_values_included": False,
    })
    proposal["secret_values_included"] = False
    return proposal
