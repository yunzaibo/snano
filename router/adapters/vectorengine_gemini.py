from __future__ import annotations

import base64
import json
import mimetypes
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from router.adapters.base import BaseAdapter
from router.core.models import (
    CapabilityBucket,
    GenerationRequest,
    GenerationResult,
    ProviderCapability,
)


class VectorEngineGeminiAdapter(BaseAdapter):
    provider_name = "vectorengine"
    default_endpoint_path = "/v1beta/models/{model}:generateContent"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.vectorengine.ai",
        timeout_seconds: int = 300,
        model_name: str = "gemini-3-pro-image-preview",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
        preflight_timeout_seconds: float | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.endpoint_path = endpoint_path or self.default_endpoint_path
        self.preflight_timeout_seconds = preflight_timeout_seconds

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider_name,
            bucket=CapabilityBucket.MULTI_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=True,
            max_reference_images=5,
            supported_sizes=["2K", "4K"],
            max_concurrency=1,
            enabled=True,
        )

    def build_request(self, request: GenerationRequest) -> dict:
        parts: list[dict] = []
        for ref in request.reference_images:
            parts.append(self._image_part(ref))
        parts.append({"text": request.prompt})

        image_config: dict[str, str] = {}
        if request.aspect_ratio:
            image_config["aspectRatio"] = request.aspect_ratio
        if request.size:
            image_config["imageSize"] = request.size
        elif request.quality == "high":
            image_config["imageSize"] = "4K"

        return {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": image_config,
            },
        }

    def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.time()
        build_started = time.time()
        url = self._endpoint_url()
        body = self.build_request(request)
        data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, method="POST")
        self._add_auth_header(req)
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "multi-image-router/1.0")
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
            timings = {
                "request_build_ms": request_build_ms,
                "provider_http_ms": provider_http_ms,
                "response_parse_ms": response_parse_ms,
                "total_ms": total_ms,
            }
            if not blobs:
                return GenerationResult(
                    ok=False,
                    provider=self.provider_name,
                    model=self.model_name,
                    request_type=request.request_type,
                    reference_count=len(request.reference_images),
                    provider_response=payload,
                    error_code="no_image",
                    error_message="No image data found in provider response.",
                    latency_ms=total_ms,
                    timings_ms=timings,
                )

            return GenerationResult(
                ok=True,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                artifact_blobs=blobs,
                provider_response=payload,
                latency_ms=total_ms,
                timings_ms=timings,
            )
        except urllib.error.HTTPError as exc:
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=False,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                error_code=type(exc).__name__,
                error_message=self._http_error_message(exc),
                latency_ms=total_ms,
                timings_ms={
                    "request_build_ms": request_build_ms,
                    "total_ms": total_ms,
                },
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

    def health_check(self) -> GenerationResult:
        started = time.time()
        timeout_seconds = self._preflight_timeout_seconds()
        try:
            parsed = urllib.parse.urlparse(self.base_url)
            host = parsed.hostname
            if not host:
                raise ValueError(f"Invalid provider base URL: {self.base_url}")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            connect_started = time.time()
            with socket.create_connection((host, port), timeout=timeout_seconds) as sock:
                connect_ms = int((time.time() - connect_started) * 1000)
                tls_ms = 0
                if parsed.scheme == "https":
                    tls_started = time.time()
                    context = ssl.create_default_context()
                    with context.wrap_socket(sock, server_hostname=host):
                        tls_ms = int((time.time() - tls_started) * 1000)
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=True,
                provider=self.provider_name,
                model=self.model_name,
                request_type="text_to_image",
                reference_count=0,
                latency_ms=total_ms,
                timings_ms={
                    "preflight_connect_ms": connect_ms,
                    "preflight_tls_ms": tls_ms,
                    "total_ms": total_ms,
                },
                provider_response={"preflight": "tls_connectivity"},
            )
        except Exception as exc:
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=False,
                provider=self.provider_name,
                model=self.model_name,
                request_type="text_to_image",
                reference_count=0,
                error_code=type(exc).__name__,
                error_message=str(exc),
                latency_ms=total_ms,
                timings_ms={"total_ms": total_ms},
                provider_response={"preflight": "tls_connectivity"},
            )

    def _preflight_timeout_seconds(self) -> float:
        if self.preflight_timeout_seconds is not None:
            return max(float(self.preflight_timeout_seconds), 0.1)
        provider = self.provider_name.lower()
        env_names = []
        if "apiyi" in provider:
            env_names.append("MIR_APIYI_PREFLIGHT_TIMEOUT_SECONDS")
        if "laozhang" in provider:
            env_names.append("MIR_LAOZHANG_PREFLIGHT_TIMEOUT_SECONDS")
        env_names.append("MIR_GEMINI_NATIVE_PREFLIGHT_TIMEOUT_SECONDS")
        for env_name in env_names:
            raw = os.environ.get(env_name)
            if raw:
                try:
                    return max(float(raw), 0.1)
                except ValueError:
                    continue
        return 8.0

    def _http_error_message(self, exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read(800).decode("utf-8", "replace").strip()
        except Exception:
            body = ""
        if body:
            return f"HTTP Error {exc.code}: {exc.reason}; body={body}"
        return str(exc)

    def _add_auth_header(self, req: urllib.request.Request) -> None:
        req.add_header("x-goog-api-key", self.api_key)

    def _endpoint_url(self) -> str:
        path = self.endpoint_path.format(model=self.model_name)
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _image_part(self, path: str) -> dict:
        raw = Path(path).read_bytes()
        mime, _ = mimetypes.guess_type(path)
        return {
            "inline_data": {
                "mime_type": mime or "image/jpeg",
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
