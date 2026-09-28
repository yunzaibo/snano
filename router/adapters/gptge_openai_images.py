from __future__ import annotations

import base64
import json
import mimetypes
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


class GptGeOpenAIImagesAdapter(BaseAdapter):
    provider_name = "gptge"
    default_endpoint_path = "/v1/images/generations"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.gpt.ge",
        timeout_seconds: int = 180,
        model_name: str = "gemini-3-pro-image-preview",
        extra_headers: dict[str, str] | None = None,
        endpoint_path: str | None = None,
        image_contract: dict | None = None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.model_name = model_name
        self.extra_headers = extra_headers or {}
        self.endpoint_path = endpoint_path or self.default_endpoint_path
        self.image_contract = image_contract or {}
        self.async_remote = False

    def capability(self) -> ProviderCapability:
        return ProviderCapability(
            provider=self.provider_name,
            bucket=CapabilityBucket.SINGLE_REFERENCE,
            supports_text_to_image=True,
            supports_image_to_image=True,
            supports_multi_reference=False,
            max_reference_images=1,
            supported_sizes=["4K"],
            max_concurrency=1,
            enabled=True,
        )

    def build_request(self, request: GenerationRequest) -> dict:
        size = self._request_size(request)
        body = {
            "model": self.model_name,
            "prompt": request.prompt,
            "size": size,
            "n": request.count,
        }
        resolution = self._request_resolution(request)
        if resolution and not self._suppresses_field("resolution"):
            body["resolution"] = resolution
        if self.image_contract.get("responseFormat"):
            body["response_format"] = str(self.image_contract["responseFormat"])
        if request.aspect_ratio and not self._suppresses_field("aspect_ratio"):
            body["aspect_ratio"] = request.aspect_ratio
        if request.negative_prompt:
            body["negative_prompt"] = request.negative_prompt
        if request.reference_images:
            image_urls = [self._reference_data_url(ref) for ref in request.reference_images]
            field_name = str(self.image_contract.get("imageUrlsField") or self.image_contract.get("image_urls_field") or "")
            if field_name:
                body[field_name] = image_urls
            else:
                body["image_url"] = image_urls[0]
        return body

    def _request_size(self, request: GenerationRequest) -> str:
        mode = str(self.image_contract.get("requestSizeMode") or self.image_contract.get("request_size_mode") or "").lower()
        default_size = self.image_contract.get("defaultSize") or self.image_contract.get("default_size")
        if mode == "aspect_ratio":
            return str(request.aspect_ratio or default_size or "1:1")
        if mode == "passthrough" and default_size and str(request.size or "").lower() in {"4k", "2k", "1254px", "default"}:
            return str(default_size)
        return str(request.size or default_size or "4K")

    def _request_resolution(self, request: GenerationRequest) -> str | None:
        mapping = self.image_contract.get("resolutionBySize") or self.image_contract.get("resolution_by_size")
        request_size = str(request.size or "").strip()
        if isinstance(mapping, dict) and request_size:
            for key, value in mapping.items():
                if str(key).strip().lower() == request_size.lower():
                    return str(value)
        default_resolution = self.image_contract.get("defaultResolution") or self.image_contract.get("default_resolution")
        return str(default_resolution) if default_resolution else None

    def _suppresses_field(self, field_name: str) -> bool:
        suppress = (
            self.image_contract.get("suppressFields")
            or self.image_contract.get("suppress_fields")
            or []
        )
        if isinstance(suppress, str):
            suppress = [suppress]
        return field_name.lower() in {str(value).strip().lower() for value in suppress}

    def _reference_data_url(self, path: str) -> str:
        if path.startswith(("http://", "https://", "data:image/")):
            return path
        mime = mimetypes.guess_type(path)[0] or "image/jpeg"
        return f"data:{mime};base64,{base64.b64encode(Path(path).read_bytes()).decode('ascii')}"

    def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.metadata.get("async_submit_only"):
            return self.submit_async(request)
        started = time.time()
        build_started = time.time()
        req = self._build_post_request(request)
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

    def submit_async(self, request: GenerationRequest) -> GenerationResult:
        started = time.time()
        build_started = time.time()
        req = self._build_post_request(request)
        request_build_ms = int((time.time() - build_started) * 1000)
        try:
            http_started = time.time()
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
            provider_http_ms = int((time.time() - http_started) * 1000)
            parse_started = time.time()
            payload = json.loads(raw)
            blobs = self._extract_sync_images(payload)
            task_id = self._extract_task_id(payload)
            response_parse_ms = int((time.time() - parse_started) * 1000)
            total_ms = int((time.time() - started) * 1000)
            if blobs:
                return GenerationResult(
                    ok=True,
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
                )
            return GenerationResult(
                ok=False,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                provider_response=payload,
                latency_ms=total_ms,
                timings_ms={
                    "request_build_ms": request_build_ms,
                    "provider_http_ms": provider_http_ms,
                    "response_parse_ms": response_parse_ms,
                    "total_ms": total_ms,
                },
                error_code="async_pending" if task_id else "no_image",
                error_message=(
                    f"Remote image task submitted and is pending: {task_id}"
                    if task_id
                    else "No image data found in provider response."
                ),
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

    def poll_async_result(self, task_id: str, request: GenerationRequest) -> GenerationResult:
        started = time.time()
        try:
            http_started = time.time()
            payload = self._fetch_task_status(task_id)
            provider_http_ms = int((time.time() - http_started) * 1000)
            parse_started = time.time()
            data = payload.get("data") if isinstance(payload, dict) else None
            status = str(data.get("status") if isinstance(data, dict) else "").lower()
            if status == "completed" and isinstance(data, dict):
                blobs = self._extract_task_result_images(data)
                response_parse_ms = int((time.time() - parse_started) * 1000)
                total_ms = int((time.time() - started) * 1000)
                return GenerationResult(
                    ok=bool(blobs),
                    provider=self.provider_name,
                    model=self.model_name,
                    request_type=request.request_type,
                    reference_count=len(request.reference_images),
                    artifact_blobs=blobs,
                    provider_response=payload,
                    latency_ms=total_ms,
                    timings_ms={
                        "provider_http_ms": provider_http_ms,
                        "response_parse_ms": response_parse_ms,
                        "total_ms": total_ms,
                    },
                    error_code=None if blobs else "no_image",
                    error_message=None if blobs else "Remote task completed without image data.",
                )
            if status in {"failed", "cancelled"}:
                total_ms = int((time.time() - started) * 1000)
                return GenerationResult(
                    ok=False,
                    provider=self.provider_name,
                    model=self.model_name,
                    request_type=request.request_type,
                    reference_count=len(request.reference_images),
                    provider_response=payload,
                    latency_ms=total_ms,
                    timings_ms={"provider_http_ms": provider_http_ms, "total_ms": total_ms},
                    error_code=f"remote_{status}",
                    error_message=f"Remote task {task_id} ended with status={status}.",
                )
            total_ms = int((time.time() - started) * 1000)
            return GenerationResult(
                ok=False,
                provider=self.provider_name,
                model=self.model_name,
                request_type=request.request_type,
                reference_count=len(request.reference_images),
                provider_response=payload,
                latency_ms=total_ms,
                timings_ms={"provider_http_ms": provider_http_ms, "total_ms": total_ms},
                error_code="async_pending",
                error_message=f"Remote task {task_id} is still pending.",
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
                timings_ms={"total_ms": total_ms},
            )

    def _build_post_request(self, request: GenerationRequest) -> urllib.request.Request:
        url = self._endpoint_url(request)
        if self._request_format(request) == "multipart":
            data, content_type = self._build_multipart_body(request)
        else:
            body = self.build_request(request)
            data = json.dumps(body).encode("utf-8")
            content_type = "application/json"
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", content_type)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "multi-image-router/1.0")
        for name, value in self.extra_headers.items():
            if value:
                req.add_header(name, value)
        return req

    def _request_format(self, request: GenerationRequest | None = None) -> str:
        if request and request.request_type == "text_to_image":
            return str(
                self.image_contract.get("textToImageRequestFormat")
                or self.image_contract.get("text_to_image_request_format")
                or "json"
            ).strip().lower()
        return str(
            self.image_contract.get("requestFormat")
            or self.image_contract.get("request_format")
            or "json"
        ).strip().lower()

    def _build_multipart_body(self, request: GenerationRequest) -> tuple[bytes, str]:
        boundary = "multi-image-router-boundary"
        parts: list[bytes] = []
        fields = self.build_request(request)
        reference_field = str(
            self.image_contract.get("referenceField")
            or self.image_contract.get("reference_field")
            or self.image_contract.get("imageUrlsField")
            or self.image_contract.get("image_urls_field")
            or "image"
        )
        fields.pop("image_url", None)
        fields.pop("image_urls", None)
        fields.pop(reference_field, None)
        for name, value in fields.items():
            parts.append(self._multipart_text_part(boundary, str(name), str(value)))
        for index, ref in enumerate(request.reference_images, start=1):
            parts.append(self._multipart_reference_part(boundary, reference_field, ref, index))
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _multipart_text_part(self, boundary: str, name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n"
        ).encode("utf-8")

    def _multipart_reference_part(self, boundary: str, name: str, ref: str, index: int) -> bytes:
        if ref.startswith(("http://", "https://", "data:image/")):
            return self._multipart_text_part(boundary, name, ref)
        path = Path(ref)
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        header = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"; filename="{path.name or f"reference-{index}"}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        return header + path.read_bytes() + b"\r\n"

    def _endpoint_url(self, request: GenerationRequest | None = None) -> str:
        path_template = self.endpoint_path
        if request and request.request_type == "text_to_image":
            path_template = str(
                self.image_contract.get("textToImagePath")
                or self.image_contract.get("text_to_image_path")
                or self.endpoint_path
            )
        path = path_template.format(model=self.model_name)
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self.base_url}{path}"

    def _http_error_message(self, exc: urllib.error.HTTPError) -> str:
        try:
            body = exc.read(800).decode("utf-8", "replace").strip()
        except Exception:
            body = ""
        if body:
            return f"HTTP Error {exc.code}: {exc.reason}; body={body}"
        return str(exc)

    def _extract_images(self, payload: dict) -> list[bytes]:
        images = self._extract_sync_images(payload)
        if images:
            return images

        task_id = self._extract_task_id(payload)
        if task_id:
            return self._poll_task_images(task_id)
        return []

    def _extract_sync_images(self, payload: dict) -> list[bytes]:
        data = payload.get("data") or []
        images: list[bytes] = []
        for item in data:
            if item.get("b64_json"):
                images.append(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                with urllib.request.urlopen(self._download_request(str(item["url"])), timeout=self.timeout_seconds) as resp:
                    images.append(resp.read())
        return images

    def _extract_task_id(self, payload: dict) -> str | None:
        data = payload.get("data")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("task_id"):
                    return str(item["task_id"])
        if isinstance(data, dict) and data.get("task_id"):
            return str(data["task_id"])
        if payload.get("task_id"):
            return str(payload["task_id"])
        return None

    def _poll_task_images(self, task_id: str) -> list[bytes]:
        deadline = time.time() + self._poll_deadline_seconds()
        last_payload: dict | None = None
        initial_delay = self._poll_initial_delay_seconds()
        if initial_delay:
            time.sleep(initial_delay)
        while time.time() < deadline:
            payload = self._fetch_task_status(task_id)
            last_payload = payload
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                time.sleep(3)
                continue
            status = str(data.get("status") or "").lower()
            if status == "completed":
                return self._extract_task_result_images(data)
            if status in {"failed", "cancelled"}:
                return []
            time.sleep(3)
        return self._extract_task_result_images(last_payload.get("data", {}) if last_payload else {})

    def _poll_deadline_seconds(self) -> int:
        configured = (
            self.image_contract.get("pollDeadlineSeconds")
            or self.image_contract.get("poll_deadline_seconds")
        )
        if configured:
            try:
                return max(int(configured), 1)
            except (TypeError, ValueError):
                pass
        return max(int(self.timeout_seconds), 1)

    def _poll_initial_delay_seconds(self) -> int:
        configured = (
            self.image_contract.get("pollInitialDelaySeconds")
            or self.image_contract.get("poll_initial_delay_seconds")
        )
        if configured:
            try:
                return max(int(configured), 0)
            except (TypeError, ValueError):
                pass
        return 0

    def _fetch_task_status(self, task_id: str) -> dict:
        encoded_task_id = urllib.parse.quote(task_id, safe="")
        poll_path = str(self.image_contract.get("pollPath") or self.image_contract.get("poll_path") or "/v1/tasks/{task_id}")
        path = poll_path.format(task_id=encoded_task_id)
        if not path.startswith("/"):
            path = f"/{path}"
        if "pollQuery" in self.image_contract:
            poll_query = str(self.image_contract.get("pollQuery") or "")
        elif "poll_query" in self.image_contract:
            poll_query = str(self.image_contract.get("poll_query") or "")
        else:
            poll_query = "language=en"
        url = f"{self.base_url}{path}"
        if poll_query:
            url = f"{url}?{poll_query.lstrip('?')}"
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "multi-image-router/1.0")
        for name, value in self.extra_headers.items():
            if value:
                req.add_header(name, value)
        with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
            return json.loads(resp.read())

    def _extract_task_result_images(self, data: dict) -> list[bytes]:
        result = data.get("result") if isinstance(data, dict) else None
        if not isinstance(result, dict):
            return []
        images: list[bytes] = []
        for image in result.get("images") or []:
            if not isinstance(image, dict):
                continue
            urls = image.get("url")
            if isinstance(urls, str):
                urls = [urls]
            for url in urls or []:
                with urllib.request.urlopen(self._download_request(str(url)), timeout=self.timeout_seconds) as resp:
                    images.append(resp.read())
        return images

    def _download_request(self, url: str) -> urllib.request.Request:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Accept", "image/*,*/*")
        req.add_header("User-Agent", "multi-image-router/1.0")
        return req
