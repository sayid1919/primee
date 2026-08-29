"""Vault read/create/append/update behaviour, atomicity and secret refusal."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode, PrimeeError
from primee.skills.vault.storage import VaultStorage

from .support import TempVaultCase


class OperationTests(TempVaultCase):
    def setUp(self):
        super().setUp()
        self.storage = VaultStorage(self.vault_root, max_file_bytes=4096)

    def test_create_then_read(self):
        written = self.storage.create("notes/idea.md", "hello\n")
        self.assertEqual(written, 6)
        self.assertEqual(self.storage.read("notes/idea.md"), "hello\n")

    def test_create_makes_parent_directories_inside_the_vault(self):
        self.storage.create("a/b/c.md", "x")
        self.assertTrue((self.vault_root / "a" / "b" / "c.md").is_file())

    def test_create_refuses_to_overwrite(self):
        self.storage.create("a.md", "first")
        with self.assertRaises(PrimeeError) as ctx:
            self.storage.create("a.md", "second")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_EXISTS)
        self.assertEqual(self.storage.read("a.md"), "first")

    def test_append_adds_a_newline_between_blocks(self):
        self.storage.create("log.md", "one")
        self.storage.append("log.md", "two")
        self.assertEqual(self.storage.read("log.md"), "one\ntwo")

    def test_append_creates_the_file_when_missing(self):
        self.storage.append("new.md", "first line")
        self.assertEqual(self.storage.read("new.md"), "first line")

    def test_update_requires_an_existing_file(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.storage.update("missing.md", "x")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_MISSING)

    def test_update_replaces_content(self):
        self.storage.create("a.md", "old")
        self.storage.update("a.md", "new")
        self.assertEqual(self.storage.read("a.md"), "new")

    def test_read_missing_file_reports_a_specific_code(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.storage.read("nope.md")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_FILE_MISSING)

    def test_there_is_no_delete_operation(self):
        self.assertFalse(hasattr(self.storage, "delete"))
        self.assertFalse(hasattr(self.storage, "remove"))

    def test_utf8_content_round_trips(self):
        self.storage.create("fa.md", "سلام دنیا\n")
        self.assertEqual(self.storage.read("fa.md"), "سلام دنیا\n")

    def test_oversized_content_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.storage.create("big.md", "x" * 5000)
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_CONTENT_REJECTED)

    def test_nul_byte_content_is_rejected(self):
        with self.assertRaises(PrimeeError):
            self.storage.create("bad.md", "a\x00b")

    def test_non_text_content_is_rejected(self):
        with self.assertRaises(PrimeeError):
            self.storage.create("bad.md", b"bytes")


class SecretRefusalTests(TempVaultCase):
    def setUp(self):
        super().setUp()
        self.storage = VaultStorage(self.vault_root)

    def assert_refused(self, content: str):
        with self.assertRaises(PrimeeError) as ctx:
            self.storage.create("note.md", content)
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_CONTENT_REJECTED)
        self.assertFalse((self.vault_root / "note.md").exists())

    def test_refuses_api_key_assignment(self):
        self.assert_refused("Notes\napi_key = SAMPLE_PLACEHOLDER_VALUE\n")

    def test_refuses_password_assignment(self):
        self.assert_refused("password: samplevalue")

    def test_refuses_private_key_block(self):
        self.assert_refused("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----")

    def test_refuses_bearer_token(self):
        self.assert_refused("Authorization header uses Bearer abcdefghijklmnopqrstuvwxyz12345")

    def test_ordinary_prose_is_accepted(self):
        self.storage.create("note.md", "Remember to renew the domain and review the plan.")
        self.assertTrue((self.vault_root / "note.md").is_file())


class AtomicityTests(TempVaultCase):
    def setUp(self):
        super().setUp()
        self.storage = VaultStorage(self.vault_root, max_file_bytes=64)

    def test_a_rejected_write_leaves_no_temporary_files_behind(self):
        self.storage.create("a.md", "small")
        with self.assertRaises(PrimeeError):
            self.storage.update("a.md", "x" * 500)
        self.assertEqual(self.storage.read("a.md"), "small")
        leftovers = [p.name for p in self.vault_root.rglob(".primee-*")]
        self.assertEqual(leftovers, [])

    def test_listing_ignores_temporary_files(self):
        (self.vault_root / ".primee-stale.tmp").write_text("junk", encoding="utf-8")
        self.storage.create("real.md", "x")
        self.assertEqual([e.path for e in self.storage.list()], ["real.md"])


if __name__ == "__main__":
    unittest.main()
