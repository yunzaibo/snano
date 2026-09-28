from __future__ import annotations

from dataclasses import dataclass

from router.core.models import CapabilityBucket, GenerationRequest, ProviderCapability


@dataclass(slots=True)
class CapabilityDecision:
    supported: bool
    reason: str
    required_reference_count: int
    max_reference_images: int
    will_downgrade_references: bool = False


def check_provider_capability(cap: ProviderCapability, request: GenerationRequest) -> CapabilityDecision:
    ref_count = len(request.reference_images)
    if not cap.enabled:
        return CapabilityDecision(False, "provider_disabled", ref_count, cap.max_reference_images)

    if request.request_type == "text_to_image":
        return CapabilityDecision(
            cap.supports_text_to_image,
            "supported" if cap.supports_text_to_image else "text_to_image_unsupported",
            ref_count,
            cap.max_reference_images,
        )

    if not cap.supports_image_to_image:
        return CapabilityDecision(False, "image_to_image_unsupported", ref_count, cap.max_reference_images)

    if ref_count <= 1:
        supported = cap.bucket in {CapabilityBucket.SINGLE_REFERENCE, CapabilityBucket.MULTI_REFERENCE}
        return CapabilityDecision(
            supported,
            "supported" if supported else "single_reference_unsupported",
            ref_count,
            cap.max_reference_images,
        )

    if cap.bucket == CapabilityBucket.MULTI_REFERENCE and cap.max_reference_images >= ref_count:
        return CapabilityDecision(True, "supported", ref_count, cap.max_reference_images)

    if request.metadata.get("requireFullReferenceLock"):
        return CapabilityDecision(False, "requires_full_reference_lock", ref_count, cap.max_reference_images)

    if cap.bucket == CapabilityBucket.SINGLE_REFERENCE and cap.max_reference_images >= 1:
        return CapabilityDecision(
            True,
            "reference_downgrade_allowed",
            ref_count,
            cap.max_reference_images,
            will_downgrade_references=True,
        )

    return CapabilityDecision(False, "multi_reference_unsupported", ref_count, cap.max_reference_images)
