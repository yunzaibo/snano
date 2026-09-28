from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from router.core.artifacts import write_result
from router.core.models import GenerationRequest, GenerationResult
from router.core.provider_state_store import ProviderHealthStore
from router.core.redaction import REDACTION, redact_text, redact_value
from router.core.scheduler import ProviderState


class RedactionTests(unittest.TestCase):
    def test_redacts_sensitive_text_patterns(self) -> None:
        message = "Authorization: Bearer sk-secret-token-123456789 and api_key=abc123456789"

        redacted = redact_text(message)

        self.assertNotIn("sk-secret-token-123456789", redacted or "")
        self.assertNotIn("abc123456789", redacted or "")
        self.assertIn(REDACTION, redacted or "")

    def test_redacts_sensitive_dict_keys_recursively(self) -> None:
        payload = {
            "error": {
                "message": "token=tok_1234567890",
                "headers": {
                    "Authorization": "Bearer secret-token-value",
                },
                "task_id": "task-secret-123456789",
                "poll_url": "https://provider.example/tasks/task-secret-123456789",
            }
        }

        redacted = redact_value(payload)

        self.assertEqual(redacted["error"]["headers"]["Authorization"], REDACTION)
        self.assertEqual(redacted["error"]["task_id"], REDACTION)
        self.assertEqual(redacted["error"]["poll_url"], REDACTION)
        self.assertNotIn("tok_1234567890", json.dumps(redacted))
        self.assertNotIn("task-secret-123456789", json.dumps(redacted))

    def test_write_result_redacts_persisted_error_and_raw_response(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            perf_log = root / "performance.jsonl"
            result = GenerationResult(
                ok=False,
                provider="fake",
                model="fake-model",
                request_type="text_to_image",
                reference_count=0,
                error_code="HTTPError",
                error_message="HTTP 401 Authorization: Bearer sk-secret-token-123456789",
                provider_response={
                    "error": "api_key=abc123456789",
                    "headers": {"cookie": "session=secret-cookie"},
                },
            )

            written = write_result(
                str(root / "artifacts"),
                "fake",
                GenerationRequest(
                    job_type="manifest",
                    request_type="text_to_image",
                    profile="generic",
                    prompt="test",
                    metadata={"token": "metadata-secret-token"},
                ),
                result,
                perf_log_path=str(perf_log),
            )

            out_dir = Path(written.raw_response_path or "").parent
            meta_text = (out_dir / "meta.json").read_text(encoding="utf-8")
            raw_text = Path(written.raw_response_path or "").read_text(encoding="utf-8")
            perf_text = perf_log.read_text(encoding="utf-8")

            combined = "\n".join([meta_text, raw_text, perf_text])
            self.assertNotIn("sk-secret-token-123456789", combined)
            self.assertNotIn("abc123456789", combined)
            self.assertNotIn("secret-cookie", combined)
            self.assertNotIn("metadata-secret-token", combined)
            self.assertIn(REDACTION, combined)

    def test_provider_health_store_redacts_last_error_message(self) -> None:
        state = ProviderState()
        ProviderHealthStore.apply_failure(
            state,
            category="auth",
            message="HTTP 401 api_key=abc123456789",
        )

        self.assertEqual(state.last_error_message, f"HTTP 401 api_key={REDACTION}")


if __name__ == "__main__":
    unittest.main()
