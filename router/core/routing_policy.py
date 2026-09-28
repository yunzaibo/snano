from __future__ import annotations

from dataclasses import dataclass, field


POLICY_SCHEMA_VERSION = "routing-policy/v1"


@dataclass(frozen=True, slots=True)
class RoutingPolicyPlan:
    policy: str
    routing_mode: str
    allow_fallback: bool
    max_retries_per_provider: int | None = None
    race_providers: list[str] = field(default_factory=list)
    provider_order: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "policy": self.policy,
            "routing_mode": self.routing_mode,
            "allow_fallback": self.allow_fallback,
            "max_retries_per_provider": self.max_retries_per_provider,
            "race_providers": list(self.race_providers),
            "provider_order": list(self.provider_order),
            "warnings": list(self.warnings),
        }


def resolve_routing_policy(policy: str | None, provider_order: list[str]) -> RoutingPolicyPlan:
    normalized = (policy or "fallback").strip().lower().replace("-", "_")
    warnings: list[str] = []
    if normalized in {"", "default"}:
        normalized = "fallback"

    if normalized == "speed_first":
        return RoutingPolicyPlan(
            policy="speed_first",
            routing_mode="race",
            allow_fallback=True,
            max_retries_per_provider=0,
            race_providers=list(provider_order),
            provider_order=list(provider_order),
        )

    if normalized == "conservative_retry":
        return RoutingPolicyPlan(
            policy="conservative_retry",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=2,
            provider_order=list(provider_order),
        )

    if normalized == "fidelity_first":
        return RoutingPolicyPlan(
            policy="fidelity_first",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=1,
            provider_order=list(provider_order),
        )

    if normalized == "cost_aware":
        return RoutingPolicyPlan(
            policy="cost_aware",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=0,
            provider_order=_cost_aware_order(provider_order),
        )

    if normalized in {"load_balance", "balanced", "throughput_balanced"}:
        return RoutingPolicyPlan(
            policy="load_balance",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=0,
            provider_order=list(provider_order),
        )

    if normalized in {"weighted_load_balance", "weighted_balanced", "performance_balanced"}:
        return RoutingPolicyPlan(
            policy="weighted_load_balance",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=0,
            provider_order=list(provider_order),
        )

    if normalized in {"health_aware_load_balance", "health_aware_balanced", "health_balanced"}:
        return RoutingPolicyPlan(
            policy="health_aware_load_balance",
            routing_mode="fallback",
            allow_fallback=True,
            max_retries_per_provider=0,
            provider_order=list(provider_order),
        )

    if normalized != "fallback":
        warnings.append(f"Unknown routing policy {policy!r}; using fallback defaults.")

    return RoutingPolicyPlan(
        policy="fallback",
        routing_mode="fallback",
        allow_fallback=True,
        max_retries_per_provider=None,
        provider_order=list(provider_order),
        warnings=warnings,
    )


def _cost_aware_order(provider_order: list[str]) -> list[str]:
    preference = {
        "aifast": 0,
        "aifast_compat": 1,
        "gptge": 2,
        "aiwave": 3,
        "vectorengine_compat": 4,
        "vectorengine": 5,
    }
    return sorted(provider_order, key=lambda provider: (preference.get(provider, 99), provider_order.index(provider)))
