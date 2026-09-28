from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


RequestType = Literal["text_to_image", "image_to_image"]
JobType = Literal["variants", "manifest"]
DedupePolicy = Literal["skip-identical", "force"]
RoutingMode = Literal["fallback", "race"]


class CapabilityBucket(str, Enum):
    TEXT_ONLY = "T"
    SINGLE_REFERENCE = "I1"
    MULTI_REFERENCE = "IM"
    EXCLUDED = "X"


@dataclass(slots=True)
class GenerationRequest:
    job_type: JobType
    request_type: RequestType
    profile: str
    prompt: str
    negative_prompt: str = ""
    reference_images: list[str] = field(default_factory=list)
    count: int = 1
    aspect_ratio: str | None = None
    size: str | None = None
    quality: str = "standard"
    allow_fallback: bool = True
    dedupe_policy: DedupePolicy = "skip-identical"
    routing_mode: RoutingMode = "fallback"
    race_providers: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderCapability:
    provider: str
    bucket: CapabilityBucket
    supports_text_to_image: bool
    supports_image_to_image: bool
    supports_multi_reference: bool
    max_reference_images: int
    supported_sizes: list[str] = field(default_factory=list)
    max_concurrency: int = 1
    enabled: bool = True


@dataclass(slots=True)
class GenerationResult:
    ok: bool
    provider: str
    model: str
    request_type: RequestType
    reference_count: int
    artifact_paths: list[str] = field(default_factory=list)
    artifact_blobs: list[bytes] = field(default_factory=list, repr=False)
    artifact_metrics: dict[str, Any] = field(default_factory=dict)
    latency_ms: int | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    raw_response_path: str | None = None
    provider_response: dict[str, Any] | None = field(default=None, repr=False)
    error_code: str | None = None
    error_message: str | None = None
