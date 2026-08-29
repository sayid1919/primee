"""The standard structured result and its validation rules."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode, ResultValidationError
from primee.core.result_types import ExternalAction, SkillResult, VaultWrite


def ok(**overrides) -> SkillResult:
    fields = {"success": True, "skill_name": "metrics", "summary": "fine"}
    fields.update(overrides)
    return SkillResult(**fields)


class ShapeTests(unittest.TestCase):
    def test_minimal_successful_result_validates(self):
        result = ok().validate()
        self.assertEqual(result.structured_data, {})
        self.assertEqual(result.proposed_vault_writes, [])
        self.assertEqual(result.proposed_external_actions, [])
        self.assertEqual(result.warnings, [])
        self.assertIsNone(result.error_code)

    def test_to_dict_contains_every_contract_field(self):
        payload = ok().validate().to_dict()
        self.assertEqual(
            sorted(payload),
            [
                "error_code",
                "proposed_external_actions",
                "proposed_vault_writes",
                "sanitized_error_message",
                "skill_name",
                "structured_data",
                "success",
                "summary",
                "warnings",
            ],
        )

    def test_rejects_bad_skill_name(self):
        for bad in ["Metrics", "", "metrics-2", 5]:
            with self.assertRaises(ResultValidationError):
                ok(skill_name=bad).validate()

    def test_rejects_empty_summary(self):
        with self.assertRaises(ResultValidationError):
            ok(summary="   ").validate()

    def test_rejects_non_serialisable_structured_data(self):
        with self.assertRaises(ResultValidationError):
            ok(structured_data={"when": object()}).validate()

    def test_rejects_success_with_error_code(self):
        with self.assertRaises(ResultValidationError):
            ok(error_code=ErrorCode.INVALID_INPUT).validate()

    def test_rejects_failure_without_error_code(self):
        with self.assertRaises(ResultValidationError):
            ok(success=False, sanitized_error_message="broken").validate()

    def test_rejects_failure_with_unknown_error_code(self):
        with self.assertRaises(ResultValidationError):
            ok(success=False, error_code="KABOOM", sanitized_error_message="x").validate()

    def test_rejects_raw_dict_instead_of_vault_write(self):
        with self.assertRaises(ResultValidationError):
            ok(proposed_vault_writes=[{"path": "a.md"}]).validate()


class VaultWriteTests(unittest.TestCase):
    def test_rejects_unknown_operation(self):
        with self.assertRaises(ResultValidationError):
            VaultWrite(path="a.md", operation="delete", content="x").validate()

    def test_permission_name_matches_the_operation(self):
        self.assertEqual(VaultWrite("a.md", "create", "x").permission, "vault.create")
        self.assertEqual(VaultWrite("a.md", "update", "x").permission, "vault.update")

    def test_describe_never_includes_the_content(self):
        described = VaultWrite("a.md", "create", "top secret note text").describe()
        self.assertNotIn("top secret", str(described))
        self.assertEqual(described["content_bytes"], len("top secret note text"))


class RedactionEnforcementTests(unittest.TestCase):
    def test_summary_is_redacted_during_validation(self):
        result = ok(summary="token: abcdefghijklmnop").validate()
        self.assertNotIn("abcdefghijklmnop", result.summary)
        self.assertIn("[REDACTED]", result.summary)

    def test_warnings_are_redacted(self):
        result = ok(warnings=["contact me at person@example.com"]).validate()
        self.assertNotIn("person@example.com", result.warnings[0])

    def test_error_messages_are_redacted(self):
        result = SkillResult.fail(
            "metrics", "failed", ErrorCode.CONNECTOR_ERROR, "api_key=SUPERSECRETVALUE"
        ).validate()
        self.assertNotIn("SUPERSECRETVALUE", result.sanitized_error_message)

    def test_external_action_description_is_redacted(self):
        action = ExternalAction(
            action="send_email",
            target="someone@example.com",
            description="reply with password=hunter2",
            required_permission="email.send",
        )
        action.validate()
        described = action.describe()
        self.assertNotIn("someone@example.com", described["target"])
        self.assertNotIn("hunter2", described["description"])


if __name__ == "__main__":
    unittest.main()
