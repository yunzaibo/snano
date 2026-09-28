from __future__ import annotations

import base64
import mimetypes
import urllib.request
from pathlib import Path

from router.adapters.vectorengine_gemini import VectorEngineGeminiAdapter
from router.core.models import GenerationRequest


class ApiyiGeminiAdapter(VectorEngineGeminiAdapter):
    provider_name = "apiyi"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.apiyi.com",
        timeout_seconds: int = 300,
        model_name: str = "gemini-3-pro-image-preview",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
        preflight_timeout_seconds: float | None = None,
    ):
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            model_name=model_name,
            extra_headers=extra_headers,
            endpoint_path=endpoint_path,
            preflight_timeout_seconds=preflight_timeout_seconds,
        )

    def build_request(self, request: GenerationRequest) -> dict:
        parts: list[dict] = [{"text": request.prompt}]
        for ref in request.reference_images:
            parts.append(self._image_part(ref))

        image_config = {
            "aspectRatio": request.aspect_ratio or "1:1",
            "imageSize": request.size or "4K",
        }
        return {
            "contents": [{"parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": image_config,
            },
        }

    def _add_auth_header(self, req: urllib.request.Request) -> None:
        req.add_header("Authorization", f"Bearer {self.api_key}")

    def _image_part(self, path: str) -> dict:
        raw = Path(path).read_bytes()
        mime, _ = mimetypes.guess_type(path)
        return {
            "inlineData": {
                "mimeType": mime or "image/jpeg",
                "data": base64.b64encode(raw).decode("ascii"),
            }
        }
