from __future__ import annotations

from dataclasses import dataclass

from router.core.models import CapabilityBucket, GenerationRequest, ProviderCapability


@dataclass(slots=True)
class ProviderState:
    inflight: int = 0
    healthy: bool = True
    cooldown_until: float = 0.0
    weight: int = 1
    cooldown_reason: str | None = None
    last_error_category: str | None = None
    last_error_message: str | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    last_recovery_signal: str | None = None
    last_probe_at: float | None = None
    consecutive_failures: int = 0


class CapabilityAwareScheduler:
    def __init__(self, registry: dict[str, ProviderCapability], states: dict[str, ProviderState]):
        self.registry = registry
        self.states = states

    def eligible_providers(self, request: GenerationRequest) -> list[str]:
        eligible: list[str] = []
        ref_count = len(request.reference_images)
        for name, cap in self.registry.items():
            state = self.states.get(name)
            if not cap.enabled or not state or not state.healthy:
                continue
            if state.inflight >= cap.max_concurrency:
                continue
            if request.request_type == "text_to_image" and cap.supports_text_to_image:
                eligible.append(name)
                continue
            if request.request_type == "image_to_image" and not cap.supports_image_to_image:
                continue
            if ref_count <= 1 and cap.bucket in {CapabilityBucket.SINGLE_REFERENCE, CapabilityBucket.MULTI_REFERENCE}:
                eligible.append(name)
                continue
            if ref_count > 1 and cap.bucket == CapabilityBucket.MULTI_REFERENCE:
                eligible.append(name)
        return eligible

    def choose_provider(self, request: GenerationRequest) -> str | None:
        pool = self.eligible_providers(request)
        if not pool:
            return None
        pool.sort(key=lambda name: (self.states[name].inflight, -self.states[name].weight, name))
        return pool[0]
