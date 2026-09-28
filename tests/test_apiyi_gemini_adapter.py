from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from router.adapters.apiyi_gemini import ApiyiGeminiAdapter
from router.core.models import GenerationRequest


class ApiyiGeminiAdapterTests(unittest.TestCase):
    def test_build_request_matches_official_nano_banana_pro_image_edit_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ref = Path(tmp) / "input.png"
            ref.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            adapter = ApiyiGeminiAdapter(api_key="test-key")

            body = adapter.build_request(
                GenerationRequest(
                    job_type="manifest",
                    request_type="image_to_image",
                    profile="generic",
                    prompt="把背景模糊化，突出前景的人物",
                    reference_images=[str(ref)],
                    aspect_ratio="16:9",
                    size="4K",
                )
            )

        parts = body["contents"][0]["parts"]
        self.assertEqual(parts[0], {"text": "把背景模糊化，突出前景的人物"})
        self.assertIn("inlineData", parts[1])
        self.assertNotIn("inline_data", parts[1])
        self.assertEqual(parts[1]["inlineData"]["mimeType"], "image/png")
        self.assertEqual(parts[1]["inlineData"]["data"], base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii"))
        self.assertEqual(body["generationConfig"]["responseModalities"], ["IMAGE"])
        self.assertEqual(body["generationConfig"]["imageConfig"], {"aspectRatio": "16:9", "imageSize": "4K"})

    def test_generate_uses_bearer_authorization_and_official_endpoint(self) -> None:
        adapter = ApiyiGeminiAdapter(api_key="test-key")
        response_payload = json.dumps({
            "candidates": [{
                "content": {
                    "parts": [{
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": base64.b64encode(b"\x89PNG\r\n\x1a\nok").decode("ascii"),
                        }
                    }]
                }
            }]
        }).encode("utf-8")

        with patch("urllib.request.urlopen") as fake_urlopen:
            fake_urlopen.return_value = _FakeResponse(response_payload)
            result = adapter.generate(
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="未来主义城市夜景",
                    aspect_ratio="1:1",
                    size="4K",
                )
            )

        sent_request = fake_urlopen.call_args.args[0]
        self.assertEqual(
            sent_request.full_url,
            "https://api.apiyi.com/v1beta/models/gemini-3-pro-image-preview:generateContent",
        )
        self.assertEqual(sent_request.headers["Authorization"], "Bearer test-key")
        self.assertNotIn("x-goog-api-key", {key.lower(): value for key, value in sent_request.headers.items()})
        self.assertTrue(result.ok)
        self.assertEqual(result.artifact_blobs, [b"\x89PNG\r\n\x1a\nok"])


class _FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.payload


if __name__ == "__main__":
    unittest.main()
