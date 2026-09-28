from __future__ import annotations

import time
from dataclasses import dataclass

from router.core.scheduler import ProviderState


@dataclass(frozen=True, slots=True)
class ProbeDecision:
    provider: str
    allowed: bool
    reason: str
    probe_type: str
    real_image_generation: bool
    next_allowed_at: float | None = None


def probe_decision(
    provider: str,
    state: ProviderState,
    *,
    requested: bool,
    now: float | None = None,
    min_interval_seconds: float = 3600.0,
) -> ProbeDecision:
    current = time.time() if now is None else now
    if state.cooldown_until > current:
        return ProbeDecision(
            provider=provider,
            allowed=False,
            reason="cooldown_active",
            probe_type="none",
            real_image_generation=False,
            next_allowed_at=state.cooldown_until,
        )

    if requested:
        return ProbeDecision(
            provider=provider,
            allowed=True,
            reason="explicit_on_demand",
            probe_type="health_check",
            real_image_generation=False,
        )

    if state.last_probe_at is not None and current - state.last_probe_at < min_interval_seconds:
        return ProbeDecision(
            provider=provider,
            allowed=False,
            reason="probe_interval_not_elapsed",
            probe_type="none",
            real_image_generation=False,
            next_allowed_at=state.last_probe_at + min_interval_seconds,
        )

    return ProbeDecision(
        provider=provider,
        allowed=True,
        reason="low_frequency_due",
        probe_type="health_check",
        real_image_generation=False,
    )
