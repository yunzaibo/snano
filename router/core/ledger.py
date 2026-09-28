from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_batch_ledger(
    ledger_dir: str,
    summary: dict[str, Any],
) -> str:
    root = Path(ledger_dir)
    root.mkdir(parents=True, exist_ok=True)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    record = {
        "summary_schema_version": summary.get("schema_version"),
        "recorded_at": _now_iso(),
        **summary,
        "schema_version": "batch-ledger/v1",
    }

    path = root / f"{ts}-{uuid.uuid4().hex[:8]}-batch-ledger.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
