from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

from router.adapters.base import BaseAdapter
from router.core.models import (
    CapabilityBucket,
    GenerationRequest,
    GenerationResult,
    ProviderCapability,
)


class AifastGeminiAdapter(BaseAdapter):
    provider_name = "aifast"
    default_endpoint_path = "/v1beta/models/{model}:generateContent"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://chat.aifast.site",
        timeout_seconds: int = 300,
        model_name: str = "gemini-3-pro-image-preview-vip",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.endpoint_path = endpoint_path or self.default_endpoint_path

    def capability(self) -> ProviderCapability:
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

    def build_request(self, request: GenerationRequest) -> dict:
        parts: list[dict] = []
        for ref in request.reference_images:
            parts.append(self._image_part(ref))
        parts.append({"text": request.prompt})
        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["TEXT", "IMAGE"],
                "imageConfig": {
                    "aspectRatio": request.aspect_ratio or "4:5",
                    "imageSize": request.size or "4K",
                }
            },
        }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.time()
        build_started = time.time()
        url = self._endpoint_url()
        body = self.build_request(request)
        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("x-goog-api-key", self.api_key)
        req.add_header("Content-Type", "application/json")
        for name, value in self.extra_headers.items():
            if value:
                req.add_header(name, value)
        request_build_ms = int((time.time() - build_started) * 1000)

        try:
            http_started = time.time()
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
            provider_http_ms = int((time.time() - http_started) * 1000)
            parse_started = time.time()
            payload = json.loads(raw)
            blobs = self._extract_images(payload)
            response_parse_ms = int((time.time() - parse_started) * 1000)
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=len(blobs) > 0,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                artifact_blobs=blobs,
                provider_response=payload,
                latency_ms=total_ms,
                timings_ms={
                    "request_build_ms": request_build_ms,
                    "provider_http_ms": provider_http_ms,
                    "response_parse_ms": response_parse_ms,
                    "total_ms": total_ms,
                },
                error_code=None if blobs else "no_image",
                error_message=None if blobs else "No image data found in provider response.",
            )
        except Exception as exc:
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=False,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                error_code=type(exc).__name__,
                error_message=str(exc),
                latency_ms=total_ms,
                timings_ms={
                    "request_build_ms": request_build_ms,
                    "total_ms": total_ms,
                },
            )

    def _endpoint_url(self) -> str:
        path = self.endpoint_path.format(model=self.model_name)
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _image_part(self, path: str) -> dict:
        raw = Path(path).read_bytes()
        return {
            "inline_data": {
                "mime_type": "image/png" if str(path).lower().endswith(".png") else "image/jpeg",
                "data": base64.b64encode(raw).decode("ascii"),
            }
        }

    def _extract_images(self, payload: dict) -> list[bytes]:
        images: list[bytes] = []
        for candidate in payload.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data") or {}
                data = inline.get("data")
                if data:
                    images.append(base64.b64decode(data))
        return images
