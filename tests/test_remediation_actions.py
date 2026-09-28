from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from router.core.remediation_actions import SAFE_ACTION_POLICY_SCHEMA_VERSION, propose_remediation_action
from router.core.remediation_audit import append_remediation_audit, read_remediation_audit


class RemediationActionTests(unittest.TestCase):
    def test_external_spend_action_requires_approval(self) -> None:
        proposal = propose_remediation_action(
            {
                "event_id": "REM-1",
                "event_type": "quota_or_balance",
                "recommended_action": "operator_check_quota_or_balance",
                "approval_required": True,
            },
            requested_action="recharge balance",
        )

        self.assertEqual(proposal["schema_version"], SAFE_ACTION_POLICY_SCHEMA_VERSION)
        self.assertEqual(proposal["safety_class"], "external_spend")
        self.assertTrue(proposal["approval_required"])
        self.assertFalse(proposal["allowed"])
        self.assertEqual(proposal["status"], "blocked_requires_approval")

    def test_local_retry_action_is_allowed_without_approval(self) -> None:
        proposal = propose_remediation_action(
            {
                "event_id": "REM-2",
                "event_type": "provider_outage",
                "recommended_action": "retry_fallback_or_cooldown_provider",
                "approval_required": False,
            }
        )

        self.assertEqual(proposal["safety_class"], "local_safe")
        self.assertFalse(proposal["approval_required"])
        self.assertTrue(proposal["allowed"])
        self.assertTrue(proposal["auto_execute"])

    def test_mixed_risk_action_uses_highest_risk_class(self) -> None:
        cases = [
            ("retry fallback after recharge balance", "external_spend"),
            ("collect evidence then rotate key", "secret_or_account"),
            ("diagnostic account panel", "external_account"),
        ]

        for action, safety_class in cases:
            with self.subTest(action=action):
                proposal = propose_remediation_action(
                    {"event_id": "REM-mixed", "approval_required": False},
                    requested_action=action,
                )

                self.assertEqual(proposal["safety_class"], safety_class)
                self.assertTrue(proposal["approval_required"])
                self.assertFalse(proposal["allowed"])
                self.assertFalse(proposal["auto_execute"])
                self.assertEqual(proposal["status"], "blocked_requires_approval")

    def test_proposal_secret_values_included_is_boolean_false(self) -> None:
        local = propose_remediation_action(
            {"event_id": "REM-local", "approval_required": False},
            requested_action="retry fallback",
        )
        approval_required = propose_remediation_action(
            {"event_id": "REM-key", "approval_required": False},
            requested_action="rotate key",
        )

        self.assertIs(local["secret_values_included"], False)
        self.assertIs(approval_required["secret_values_included"], False)

    def test_audit_log_redacts_secret_like_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            proposal = propose_remediation_action(
                {"event_id": "REM-3", "recommended_action": "fix key"},
                requested_action="rotate key sk-test-secret",
            )
            append_remediation_audit(str(path), proposal, evidence={"token": "sk-test-secret"})
            rows = read_remediation_audit(str(path))
            text = path.read_text(encoding="utf-8")

        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]["secret_values_included"])
        self.assertNotIn("sk-test-secret", text)


if __name__ == "__main__":
    unittest.main()
