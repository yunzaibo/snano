from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import patch

from router.adapters.vectorengine_gemini import VectorEngineGeminiAdapter
from router.core.models import GenerationRequest


class VectorEngineGeminiAdapterTests(unittest.TestCase):
    def test_generate_adds_cloudflare_safe_headers(self) -> None:
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"candidates":[]}'

        def fake_urlopen(req, timeout):
            captured["headers"] = dict(req.header_items())
            return FakeResponse()

        adapter = VectorEngineGeminiAdapter(api_key="test-key", base_url="https://right.codes/gemini")
        request = GenerationRequest(
            job_type="manifest",
            request_type="text_to_image",
            profile="generic",
            prompt="test prompt",
            size="1K",
        )

        with patch("router.adapters.vectorengine_gemini.urllib.request.urlopen", fake_urlopen):
            adapter.generate(request)

        self.assertEqual(captured["headers"]["User-agent"], "multi-image-router/1.0")
        self.assertEqual(captured["headers"]["Accept"], "application/json")

    def test_http_error_message_includes_provider_body(self) -> None:
        adapter = VectorEngineGeminiAdapter(api_key="test-key", base_url="https://right.codes/gemini")
        request = GenerationRequest(
            job_type="manifest",
            request_type="text_to_image",
            profile="generic",
            prompt="test prompt",
        )
        error = urllib.error.HTTPError(
            url="https://right.codes/gemini/v1beta/models/x:generateContent",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"model not configured"}'),
        )

        with patch("router.adapters.vectorengine_gemini.urllib.request.urlopen", side_effect=error):
            result = adapter.generate(request)

        self.assertFalse(result.ok)
        self.assertEqual(result.error_code, "HTTPError")
        self.assertIn("model not configured", result.error_message or "")


if __name__ == "__main__":
    unittest.main()
