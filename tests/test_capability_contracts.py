from __future__ import annotations

import tempfile
import threading
import unittest

from router.core.batch_runner import _execute_single_request
from router.core.models import CapabilityBucket, GenerationRequest, GenerationResult, ProviderCapability
from router.core.provider_contracts import check_provider_capability
from router.core.scheduler import ProviderState


class SingleRefOnlyAdapter:
    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider="single-ref",
            bucket=CapabilityBucket.SINGLE_REFERENCE,
            supports_text_to_image=False,
            supports_image_to_image=True,
            supports_multi_reference=False,
            max_reference_images=1,
            max_concurrency=1,
            enabled=True,
        )

    def generate(self, request: GenerationRequest) -> GenerationResult:
        raise AssertionError("unsupported full-reference request should not call provider")


def multi_ref_locked_request(reference_images: list[str] | None = None) -> GenerationRequest:
    return GenerationRequest(
        job_type="manifest",
        request_type="image_to_image",
        profile="generic",
        prompt="keep both references",
        reference_images=reference_images or ["a.png", "b.png"],
        metadata={"requireFullReferenceLock": True},
    )


class CapabilityContractTests(unittest.TestCase):
    def test_full_reference_lock_rejects_single_reference_provider(self) -> None:
        cap = SingleRefOnlyAdapter().capability()

        decision = check_provider_capability(cap, multi_ref_locked_request())

        self.assertFalse(decision.supported)
        self.assertEqual(decision.reason, "requires_full_reference_lock")
        self.assertEqual(decision.required_reference_count, 2)
        self.assertEqual(decision.max_reference_images, 1)

    def test_five_reference_full_lock_excludes_single_reference_lanes(self) -> None:
        cap = SingleRefOnlyAdapter().capability()

        decision = check_provider_capability(
            cap,
            multi_ref_locked_request(["1.png", "2.png", "3.png", "4.png", "5.png"]),
        )

        self.assertFalse(decision.supported)
        self.assertEqual(decision.reason, "requires_full_reference_lock")
        self.assertEqual(decision.required_reference_count, 5)
        self.assertEqual(decision.max_reference_images, 1)
        self.assertFalse(decision.will_downgrade_references)

    def test_unsupported_capability_fails_before_provider_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ref_a = f"{tmp}/a.png"
            ref_b = f"{tmp}/b.png"
            with open(ref_a, "wb") as handle:
                handle.write(b"a")
            with open(ref_b, "wb") as handle:
                handle.write(b"b")
            adapters = {"single-ref": SingleRefOnlyAdapter()}
            registry = {"single-ref": adapters["single-ref"].capability()}
            states = {"single-ref": ProviderState()}

            result = _execute_single_request(
                multi_ref_locked_request([ref_a, ref_b]),
                item_id="item-001",
                adapters=adapters,
                output_dir=tmp,
                provider_preference=["single-ref"],
                registry=registry,
                states=states,
                lock=threading.Lock(),
                max_retries_per_provider=0,
                retry_delay_seconds=0,
            )

            self.assertEqual(result.status, "failed")
            self.assertEqual(result.error_code, "unsupported_capability")
            self.assertEqual(result.skipped_providers[0]["reason"], "requires_full_reference_lock")


if __name__ == "__main__":
    unittest.main()
