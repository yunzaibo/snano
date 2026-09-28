from __future__ import annotations

from router.adapters.gptge_openai_images import GptGeOpenAIImagesAdapter


class LaozhangOpenAIImagesAdapter(GptGeOpenAIImagesAdapter):
    provider_name = "laozhang"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.laozhang.ai",
        timeout_seconds: int = 180,
        model_name: str = "gemini-3-pro-image-preview",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
        image_contract: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            extra_headers=extra_headers,
            endpoint_path=endpoint_path,
            image_contract=image_contract,
        )


class ApiyiOpenAIImagesAdapter(GptGeOpenAIImagesAdapter):
    provider_name = "apiyi"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.apiyi.com",
        timeout_seconds: int = 180,
        model_name: str = "gemini-3-pro-image-preview-4k",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
        image_contract: dict | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            extra_headers=extra_headers,
            endpoint_path=endpoint_path,
            image_contract=image_contract,
        )
