from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from router.core.redaction import redact_value


REMEDIATION_AUDIT_SCHEMA_VERSION = "remediation-audit/v1"
_WRITE_LOCK = threading.Lock()


def append_remediation_audit(
    path: str,
    proposal: dict[str, Any],
    *,
    actor: str = "operator",
    evidence: dict[str, Any] | None = None,
) -> str:
    payload = redact_value({
        "schema_version": REMEDIATION_AUDIT_SCHEMA_VERSION,
        "recorded_at": time.time(),
        "actor": actor,
        "event_id": proposal.get("event_id"),
        "action": proposal.get("action"),
        "status": proposal.get("status"),
        "policy_decision": proposal.get("policy_decision"),
        "approval_required": proposal.get("approval_required"),
        "approved": proposal.get("approved"),
        "allowed": proposal.get("allowed"),
        "safety_class": proposal.get("safety_class"),
        "proposal": proposal,
        "evidence": evidence or {},
    })
    payload["secret_values_included"] = False
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK:
        with out_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return str(out_path)


def read_remediation_audit(path: str) -> list[dict[str, Any]]:
    candidate = Path(path)
    if not candidate.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in candidate.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows
