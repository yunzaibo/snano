from __future__ import annotations

import io
import json
import urllib.error
import unittest
from unittest.mock import patch

from router.adapters.gptge_openai_images import GptGeOpenAIImagesAdapter
from router.core.health import ProviderErrorCategory, classify_generation_failure
from router.core.models import GenerationRequest


class OpenAIImagesAdapterTests(unittest.TestCase):
    def test_http_error_body_is_preserved_for_quota_classification(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.vectorengine.ai",
            model_name="gemini-3.1-flash-image-preview",
        )
        error = urllib.error.HTTPError(
            url="https://api.vectorengine.ai/v1/images/generations",
            code=403,
            msg="Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"error":{"code":"local:insufficient_quota","message":"need quota"}}'),
        )

        with patch("urllib.request.urlopen", side_effect=error):
            result = adapter.generate(
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test prompt",
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "HTTPError")
        self.assertIn("insufficient_quota", result.error_message or "")
        decision = classify_generation_failure(result)
        self.assertEqual(decision.category, ProviderErrorCategory.RATE_LIMIT)
        self.assertEqual(decision.remediation_hint, "gptge_quota_or_balance_exhausted_check_panel_before_retry")

    def test_extracts_image_bytes_from_url_response(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://www.right.codes/draw",
            model_name="gpt-image-2",
            image_contract={"responseFormat": "url"},
        )
        api_response = json.dumps({
            "created": 1780467609,
            "data": [{"url": "https://files.example.test/image.png"}],
        }).encode("utf-8")
        image_bytes = b"\x89PNG\r\n\x1a\nfake"

        with patch("urllib.request.urlopen") as fake_urlopen:
            fake_urlopen.side_effect = [
                _FakeResponse(api_response),
                _FakeResponse(image_bytes),
            ]
            result = adapter.generate(
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test prompt",
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.artifact_blobs, [image_bytes])
        self.assertEqual(fake_urlopen.call_count, 2)

    def test_rightcodes_snano_images_contract_uses_pixel_size_and_url_format(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://www.right.codes/draw",
            model_name="gemini-3-pro-image-preview",
            image_contract={
                "requestSizeMode": "passthrough",
                "defaultSize": "4096x4096",
                "responseFormat": "url",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="4K",
            )
        )

        self.assertEqual(body["model"], "gemini-3-pro-image-preview")
        self.assertEqual(body["size"], "4096x4096")
        self.assertEqual(body["response_format"], "url")

    def test_apimart_simage_contract_uses_ratio_and_2k_resolution(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            image_contract={
                "requestSizeMode": "aspect_ratio",
                "defaultSize": "1:1",
                "defaultResolution": "2k",
                "resolutionBySize": {"2K": "2k", "4K": "4K"},
                "pollPath": "/v1/tasks/{task_id}",
                "pollQuery": "",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="2K",
                aspect_ratio="1:1",
            )
        )

        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "1:1")
        self.assertEqual(body["resolution"], "2k")

    def test_apiyi_official_simage_contract_uses_size_without_response_format(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apiyi.com/v1",
            model_name="gpt-image-2",
            image_contract={
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="2K",
            )
        )

        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "2048x2048")
        self.assertNotIn("response_format", body)
        self.assertNotIn("resolution", body)

    def test_apiyi_official_simage_i2i_contract_sends_multipart_multi_reference(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apiyi.com/v1",
            model_name="gpt-image-2",
            endpoint_path="/images/edits",
            image_contract={
                "textToImagePath": "/images/generations",
                "requestFormat": "multipart",
                "referenceField": "image",
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
                "suppressFields": ["aspect_ratio"],
            },
        )

        req = adapter._build_post_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                ],
                size="2K",
                aspect_ratio="1:1",
            )
        )

        body = req.data or b""
        self.assertIn("multipart/form-data", req.get_header("Content-type"))
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gpt-image-2", body)
        self.assertIn(b'name="size"', body)
        self.assertIn(b"2048x2048", body)
        self.assertNotIn(b'name="response_format"', body)
        self.assertNotIn(b'name="aspect_ratio"', body)
        self.assertEqual(body.count(b'name="image"'), 3)

    def test_apiyi_official_simage_t2i_uses_generations_json_not_edits_multipart(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apiyi.com/v1",
            model_name="gpt-image-2",
            endpoint_path="/images/edits",
            image_contract={
                "textToImagePath": "/images/generations",
                "requestFormat": "multipart",
                "referenceField": "image",
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
                "suppressFields": ["aspect_ratio"],
            },
        )

        req = adapter._build_post_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="2K",
                aspect_ratio="1:1",
            )
        )

        body = json.loads((req.data or b"{}").decode("utf-8"))
        self.assertEqual(req.full_url, "https://api.apiyi.com/v1/images/generations")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "2048x2048")
        self.assertNotIn("image", body)
        self.assertNotIn("aspect_ratio", body)

    def test_apimart_snano_contract_uses_ratio_and_4k_resolution(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gemini-3-pro-image-preview",
            image_contract={
                "requestSizeMode": "aspect_ratio",
                "defaultSize": "1:1",
                "defaultResolution": "4K",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="4K",
                aspect_ratio="1:1",
            )
        )

        self.assertEqual(body["model"], "gemini-3-pro-image-preview")
        self.assertEqual(body["size"], "1:1")
        self.assertEqual(body["resolution"], "4K")

    def test_apimart_snano_i2i_contract_sends_multiple_references_in_image_urls(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gemini-3-pro-image-preview",
            image_contract={
                "requestSizeMode": "aspect_ratio",
                "defaultSize": "1:1",
                "defaultResolution": "4K",
                "imageUrlsField": "image_urls",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABg",
                    "https://example.com/3.png",
                    "https://example.com/4.webp",
                    "https://example.com/5.jpeg",
                ],
                size="4K",
                aspect_ratio="1:1",
            )
        )

        self.assertEqual(body["image_urls"], [
            "https://example.com/1.jpg",
            "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQEAYABg",
            "https://example.com/3.png",
            "https://example.com/4.webp",
            "https://example.com/5.jpeg",
        ])
        self.assertEqual(body["resolution"], "4K")

    def test_simage_i2i_contract_sends_five_references_with_2k_resolution(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            image_contract={
                "requestSizeMode": "aspect_ratio",
                "defaultSize": "1:1",
                "defaultResolution": "2k",
                "resolutionBySize": {"2K": "2k", "4K": "4K"},
                "imageUrlsField": "image_urls",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                    "https://example.com/5.jpg",
                ],
                size="2K",
                aspect_ratio="16:9",
                metadata={"requireFullReferenceLock": True},
            )
        )

        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "16:9")
        self.assertEqual(body["resolution"], "2k")
        self.assertEqual(len(body["image_urls"]), 5)

    def test_rightcodes_simage_i2i_contract_uses_2k_pixels_and_five_images_field(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://www.right.codes/draw",
            model_name="gpt-image-2",
            image_contract={
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
                "responseFormat": "url",
                "imageUrlsField": "image",
                "resolutionBySize": {"2K": "2048x2048", "4K": "4096x4096"},
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                    "https://example.com/5.jpg",
                ],
                size="2K",
                aspect_ratio="16:9",
                metadata={"requireFullReferenceLock": True},
            )
        )

        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "2048x2048")
        self.assertEqual(body["resolution"], "2048x2048")
        self.assertEqual(body["response_format"], "url")
        self.assertEqual(len(body["image"]), 5)

    def test_laozhang_simage_edits_contract_uses_multipart_with_repeated_image_fields(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.laozhang.ai/v1",
            model_name="gpt-image-2",
            endpoint_path="/images/edits",
            image_contract={
                "textToImagePath": "/images/generations",
                "requestFormat": "multipart",
                "referenceField": "image",
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
                "resolutionBySize": {"2K": "2048x2048", "4K": "4096x4096"},
                "suppressFields": ["resolution", "aspect_ratio"],
            },
        )

        req = adapter._build_post_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                    "https://example.com/5.jpg",
                ],
                size="2K",
                aspect_ratio="16:9",
                metadata={"requireFullReferenceLock": True},
            )
        )

        body = req.data or b""
        self.assertIn("multipart/form-data", req.get_header("Content-type"))
        self.assertIn(b'name="model"', body)
        self.assertIn(b"gpt-image-2", body)
        self.assertIn(b'name="size"', body)
        self.assertIn(b"2048x2048", body)
        self.assertNotIn(b'name="resolution"', body)
        self.assertNotIn(b'name="aspect_ratio"', body)
        self.assertEqual(body.count(b'name="image"'), 5)

    def test_laozhang_simage_t2i_uses_generations_json_not_edits_multipart(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.laozhang.ai/v1",
            model_name="gpt-image-2",
            endpoint_path="/images/edits",
            image_contract={
                "textToImagePath": "/images/generations",
                "requestFormat": "multipart",
                "referenceField": "image",
                "requestSizeMode": "passthrough",
                "defaultSize": "2048x2048",
                "suppressFields": ["resolution", "aspect_ratio"],
            },
        )

        req = adapter._build_post_request(
            GenerationRequest(
                job_type="manifest",
                request_type="text_to_image",
                profile="generic",
                prompt="test prompt",
                size="2K",
                aspect_ratio="16:9",
            )
        )

        body = json.loads((req.data or b"{}").decode("utf-8"))
        self.assertEqual(req.full_url, "https://api.laozhang.ai/v1/images/generations")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        self.assertEqual(body["model"], "gpt-image-2")
        self.assertEqual(body["size"], "2048x2048")
        self.assertNotIn("image", body)
        self.assertNotIn("resolution", body)
        self.assertNotIn("aspect_ratio", body)

    def test_apimart_poll_can_delay_before_first_task_status_query(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            timeout_seconds=10,
            image_contract={
                "pollInitialDelaySeconds": 15,
                "pollPath": "/v1/tasks/{task_id}",
                "pollQuery": "language=zh",
            },
        )

        with patch("time.sleep") as fake_sleep, patch.object(adapter, "_fetch_task_status", return_value={
            "code": 200,
            "data": {
                "id": "task_123",
                "status": "completed",
                "result": {"images": []},
            },
        }):
            adapter._poll_task_images("task_123")

        fake_sleep.assert_any_call(15)

    def test_apimart_i2i_translates_4k_and_ratio_without_sending_size_4k(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gemini-3-pro-image-preview",
            image_contract={
                "requestSizeMode": "aspect_ratio",
                "defaultSize": "1:1",
                "defaultResolution": "4K",
                "imageUrlsField": "image_urls",
            },
        )

        body = adapter.build_request(
            GenerationRequest(
                job_type="manifest",
                request_type="image_to_image",
                profile="generic",
                prompt="test prompt",
                reference_images=[
                    "https://example.com/1.jpg",
                    "https://example.com/2.jpg",
                    "https://example.com/3.jpg",
                    "https://example.com/4.jpg",
                    "https://example.com/5.jpg",
                ],
                size="4K",
                aspect_ratio="16:9",
                metadata={"requireFullReferenceLock": True},
            )
        )

        self.assertEqual(body["size"], "16:9")
        self.assertEqual(body["resolution"], "4K")
        self.assertNotEqual(body["size"], "4K")
        self.assertEqual(len(body["image_urls"]), 5)

    def test_polls_async_task_response_and_downloads_image(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            timeout_seconds=10,
        )
        submit_response = json.dumps({
            "code": 200,
            "data": [{"status": "submitted", "task_id": "task_123"}],
        }).encode("utf-8")
        poll_response = json.dumps({
            "code": 200,
            "data": {
                "id": "task_123",
                "status": "completed",
                "result": {"images": [{"url": ["https://files.example.test/image.png"]}]},
            },
        }).encode("utf-8")
        image_bytes = b"\x89PNG\r\n\x1a\nasync"

        with patch("urllib.request.urlopen") as fake_urlopen:
            fake_urlopen.side_effect = [
                _FakeResponse(submit_response),
                _FakeResponse(poll_response),
                _FakeResponse(image_bytes),
            ]
            result = adapter.generate(
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test prompt",
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.artifact_blobs, [image_bytes])
        self.assertEqual(fake_urlopen.call_count, 3)

    def test_submit_async_returns_pending_without_polling(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            timeout_seconds=10,
        )
        submit_response = json.dumps({
            "code": 200,
            "data": [{"status": "submitted", "task_id": "task_123"}],
        }).encode("utf-8")

        with patch("urllib.request.urlopen") as fake_urlopen:
            fake_urlopen.return_value = _FakeResponse(submit_response)
            result = adapter.submit_async(
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test prompt",
                )
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "async_pending")
        self.assertEqual(fake_urlopen.call_count, 1)

    def test_poll_async_result_checks_once(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gpt-image-2",
            timeout_seconds=10,
            image_contract={
                "pollPath": "/v1/tasks/{task_id}",
                "pollQuery": "language=zh",
            },
        )
        poll_response = json.dumps({
            "code": 200,
            "data": {"id": "task_123", "status": "processing"},
        }).encode("utf-8")

        seen_urls = []

        def fake_open(req, timeout=None):
            seen_urls.append(req.full_url)
            return _FakeResponse(poll_response)

        with patch("urllib.request.urlopen", side_effect=fake_open) as fake_urlopen:
            result = adapter.poll_async_result(
                "task_123",
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test prompt",
                ),
            )

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "async_pending")
        self.assertEqual(fake_urlopen.call_count, 1)
        self.assertEqual(seen_urls, ["https://api.apimart.ai/v1/tasks/task_123?language=zh"])

    def test_async_poll_deadline_can_be_shorter_than_provider_request_timeout(self) -> None:
        adapter = GptGeOpenAIImagesAdapter(
            api_key="test-key",
            base_url="https://api.apimart.ai",
            model_name="gemini-3-pro-image-preview",
            timeout_seconds=300,
            image_contract={"pollDeadlineSeconds": 150},
        )

        self.assertEqual(adapter._poll_deadline_seconds(), 150)


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.body


if __name__ == "__main__":
    unittest.main()
