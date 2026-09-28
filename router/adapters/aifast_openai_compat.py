from __future__ import annotations

import base64
import json
import mimetypes
import re
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


class AifastOpenAICompatAdapter(BaseAdapter):
    provider_name = "aifast_compat"
    default_endpoint_path = "/v1/chat/completions"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://aifast.site",
        timeout_seconds: int = 300,
        model_name: str = "gemini-3-pro-image-preview-4k",
        endpoint_path: str | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.endpoint_path = endpoint_path or self.default_endpoint_path

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider_name,
            bucket=CapabilityBucket.EXCLUDED,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=True,
            max_reference_images=5,
            supported_sizes=["1024x1024", "1536x1024", "1024x1536", "1792x1024", "1024x1792"],
            max_concurrency=1,
            enabled=True,
        )

    def build_request(self, request: GenerationRequest) -> dict:
        content: list[dict] = [{"type": "text", "text": request.prompt}]
        for ref in request.reference_images:
            mime = mimetypes.guess_type(ref)[0] or "image/jpeg"
            data = base64.b64encode(Path(ref).read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{data}"},
            })
        return {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
            "stream": False,
        }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.time()
        build_started = time.time()
        url = self._endpoint_url()
        body = self.build_request(request)
        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
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

    def _extract_images(self, payload: dict) -> list[bytes]:
        images: list[bytes] = []
        for choice in payload.get("choices", []):
            msg = choice.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        images.extend(self._decode_data_url(url))
            elif isinstance(content, str):
                for match in re.finditer(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content):
                    try:
                        images.append(base64.b64decode(match.group(1)))
                    except Exception:
                        continue
        return images

    def _decode_data_url(self, url: str) -> list[bytes]:
        if url.startswith("data:") and "," in url:
            try:
                return [base64.b64decode(url.split(",", 1)[1])]
            except Exception:
                return []
        return []
