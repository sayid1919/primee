"""Secret and personal-data redaction (all values below are synthetic)."""

from __future__ import annotations

import unittest

from primee.core.redaction import REDACTED, is_sensitive_key, redact_structure, redact_text


class TextRedactionTests(unittest.TestCase):
    def assert_hidden(self, text: str, needle: str):
        result = redact_text(text)
        self.assertNotIn(needle, result, msg=f"{needle!r} survived in {result!r}")

    def test_hides_key_value_credentials(self):
        cases = [
            ("api_key=PLACEHOLDERVALUE1", "PLACEHOLDERVALUE1"),
            ("password: PLACEHOLDERVALUE2", "PLACEHOLDERVALUE2"),
            ('client_secret = "PLACEHOLDERVALUE3"', "PLACEHOLDERVALUE3"),
            ("refresh_token: PLACEHOLDERVALUE4", "PLACEHOLDERVALUE4"),
            ("recovery_code=PLACEHOLDERVALUE5", "PLACEHOLDERVALUE5"),
        ]
        for text, needle in cases:
            self.assert_hidden(text, needle)

    def test_hides_bearer_tokens(self):
        self.assert_hidden("Authorization: Bearer PLACEHOLDER.TOKEN.VALUE", "PLACEHOLDER.TOKEN.VALUE")

    def test_hides_private_key_blocks(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nPLACEHOLDERBODY\n-----END RSA PRIVATE KEY-----"
        self.assert_hidden(text, "PLACEHOLDERBODY")

    def test_hides_basic_auth_in_urls(self):
        self.assert_hidden("https://user:PLACEHOLDERPASS@example.invalid/path", "PLACEHOLDERPASS")

    def test_hides_email_addresses(self):
        self.assert_hidden("write to sample.person@example.invalid today", "sample.person@example.invalid")

    def test_hides_long_digit_runs(self):
        self.assert_hidden("card 4111111111111111 was used", "4111111111111111")

    def test_hides_the_windows_account_name(self):
        self.assert_hidden(r"C:\Users\SampleAccount\Documents\note.md", "SampleAccount")

    def test_hides_the_posix_account_name(self):
        self.assert_hidden("/home/sampleaccount/notes", "sampleaccount")

    def test_leaves_ordinary_text_alone(self):
        text = "The morning brief has three items and the plan is ready."
        self.assertEqual(redact_text(text), text)

    def test_truncates_very_long_text(self):
        self.assertIn("[TRUNCATED]", redact_text("a" * 20000))

    def test_non_string_input_is_returned_unchanged(self):
        self.assertEqual(redact_text(5), 5)


class KeyRedactionTests(unittest.TestCase):
    def test_recognises_sensitive_keys(self):
        for key in ["password", "API_KEY", "Client Secret", "session_id", "seed_phrase", "cvv"]:
            self.assertTrue(is_sensitive_key(key), msg=key)

    def test_leaves_ordinary_keys_alone(self):
        for key in ["summary", "path", "count", "observed_at"]:
            self.assertFalse(is_sensitive_key(key), msg=key)

    def test_sensitive_values_are_removed_whatever_they_look_like(self):
        redacted = redact_structure({"api_key": 12345, "note": "fine"})
        self.assertEqual(redacted["api_key"], REDACTED)
        self.assertEqual(redacted["note"], "fine")

    def test_redaction_recurses_into_nested_structures(self):
        payload = {
            "outer": {"inner": [{"token": "PLACEHOLDERVALUE"}, "mail me at a@b.invalid"]},
        }
        redacted = redact_structure(payload)
        self.assertEqual(redacted["outer"]["inner"][0]["token"], REDACTED)
        self.assertNotIn("a@b.invalid", str(redacted))

    def test_deeply_nested_structures_are_capped(self):
        payload = current = {}
        for _ in range(40):
            child = {}
            current["next"] = child
            current = child
        self.assertIsNotNone(redact_structure(payload))

    def test_numbers_and_booleans_survive(self):
        redacted = redact_structure({"count": 3, "ok": True, "ratio": 0.5, "none": None})
        self.assertEqual(redacted, {"count": 3, "ok": True, "ratio": 0.5, "none": None})


if __name__ == "__main__":
    unittest.main()
