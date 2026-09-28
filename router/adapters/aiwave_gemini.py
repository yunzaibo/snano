from __future__ import annotations

from router.adapters.aifast_gemini import AifastGeminiAdapter


class AiWaveGeminiAdapter(AifastGeminiAdapter):
    provider_name = "aiwave"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api2.ai-wave.org/gemini",
        timeout_seconds: int = 300,
        model_name: str = "gemini-3-pro-image-preview",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            extra_headers=extra_headers,
            endpoint_path=endpoint_path,
        )

    def capability(self):
        from router.core.models import CapabilityBucket, ProviderCapability

        return ProviderCapability(
            provider=self.provider_name,
            bucket=CapabilityBucket.SINGLE_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=False,
            max_reference_images=1,
            supported_sizes=["1K", "2K", "4K"],
            max_concurrency=1,
            enabled=True,
        )
