"""Security of the memory layer: path escapes, credentials, reserved files."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, PathRejectedError, PrimeeError
from primee.memory.schema import CHANGELOG_FILE, INDEX_FILE, PRIMEE_FILE

from .memory_support import MemoryCase
from .test_vault_paths import symlinks_supported


class PathEscapeTests(MemoryCase):
    def test_traversal_in_a_page_read_is_blocked(self):
        (self.outside / "secret.md").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(PathRejectedError):
            self.memory.page_at("../outside/secret.md")

    def test_absolute_path_read_is_blocked(self):
        target = self.outside / "secret.md"
        target.write_text("synthetic", encoding="utf-8")
        with self.assertRaises(PathRejectedError):
            self.memory.page_at(str(target))

    def test_a_write_outside_the_vault_is_blocked(self):
        with self.assertRaises(PathRejectedError):
            self.memory._write_page(
                "../escaped.md", self._any_metadata(), "body", mode="create"
            )
        self.assertFalse((self.tmp_path / "escaped.md").exists())

    def test_generated_paths_always_stay_inside_the_vault(self):
        result = self.make_raw(title="../../escape attempt")
        self.assertTrue(result.data["path"].startswith("raw/"))
        self.assertNotIn("..", result.data["path"])
        resolved = (self.vault_root / result.data["path"]).resolve()
        self.assertTrue(str(resolved).startswith(str(self.vault_root.resolve())))

    def test_a_title_cannot_inject_a_directory(self):
        result = self.make_raw(title="a/b/c")
        self.assertEqual(result.data["path"].count("/"), 1)

    @unittest.skipUnless(
        symlinks_supported(Path(os.environ.get("TMPDIR", "/tmp"))),
        "This platform or account cannot create symbolic links.",
    )
    def test_symlink_escape_is_blocked(self):
        (self.outside / "loot").mkdir()
        victim = self.outside / "loot" / "victim.md"
        victim.write_text("original", encoding="utf-8")
        (self.vault_root / "wiki" / "escape").symlink_to(
            self.outside / "loot", target_is_directory=True
        )
        with self.assertRaises(PathRejectedError):
            self.memory.page_at("wiki/escape/victim.md")
        self.assertEqual(victim.read_text(encoding="utf-8"), "original")

    def test_a_reserved_windows_name_is_refused(self):
        with self.assertRaises(PathRejectedError):
            self.memory._guard_reserved("raw/CON.md")

    def _any_metadata(self):
        from primee.memory.schema import build_metadata

        return build_metadata(
            {
                "id": "raw-20260827-0123456789",
                "schema_version": 1,
                "title": "t",
                "type": "raw_note",
                "tags": [],
                "created": "2026-08-27T08:00:00+03:30",
                "updated": "2026-08-27T08:00:00+03:30",
                "summary": "s",
            }
        )


class ReservedFileTests(MemoryCase):
    def test_managed_root_files_cannot_be_written_directly(self):
        for name in (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE):
            with self.assertRaises(PrimeeError, msg=name) as ctx:
                self.memory._guard_reserved(name)
            self.assertEqual(ctx.exception.code, ErrorCode.VAULT_PATH_REJECTED)
            self.assertIn("managed by Primee", ctx.exception.message)

    def test_the_changelog_survives_an_attempt_to_target_it(self):
        before = self.read_raw_file(CHANGELOG_FILE)
        with self.assertRaises(PrimeeError):
            self.memory._guard_reserved(CHANGELOG_FILE)
        self.assertEqual(self.read_raw_file(CHANGELOG_FILE), before)

    def test_root_files_are_not_treated_as_pages(self):
        self.make_wiki()
        paths = [page.relative_path for page in self.memory.pages(refresh=True)]
        for name in (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE):
            self.assertNotIn(name, paths)


class CredentialTests(MemoryCase):
    def assert_refused(self, body: str):
        with self.assertRaises(PrimeeError) as ctx:
            self.make_raw(body=body)
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_CONTENT_REJECTED)
        self.assertIn("credential", ctx.exception.message)

    def test_an_api_key_in_a_page_body_is_refused(self):
        self.assert_refused("Notes\napi_key = SAMPLE_PLACEHOLDER_VALUE\n")

    def test_a_password_in_a_page_body_is_refused(self):
        self.assert_refused("password: samplevalue")

    def test_a_private_key_block_is_refused(self):
        self.assert_refused("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----")

    def test_a_seed_phrase_assignment_is_refused(self):
        self.assert_refused("seed_phrase = one two three four five six")

    def test_a_refused_page_is_not_created_at_all(self):
        before = self.vault_files()
        with self.assertRaises(PrimeeError):
            self.make_raw(body="api_key = SAMPLE_PLACEHOLDER_VALUE")
        self.assertEqual(self.vault_files(), before)

    def test_ordinary_prose_is_accepted(self):
        result = self.make_raw(body="Remember to renew the domain before Friday.")
        self.assertTrue(result.ok)

    def test_a_refusal_message_does_not_echo_the_credential(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.make_raw(body="api_key = UNIQUEPLACEHOLDER98765")
        self.assertNotIn("UNIQUEPLACEHOLDER98765", ctx.exception.sanitized_message())


class NoDeleteTests(MemoryCase):
    def test_the_memory_service_exposes_no_delete_operation(self):
        for forbidden in ("delete", "remove", "unlink", "destroy", "purge", "wipe"):
            self.assertFalse(hasattr(self.memory, forbidden), msg=forbidden)

    def test_the_operation_catalogue_contains_no_delete(self):
        from primee.memory.operations import OPERATION_NAMES

        for name in OPERATION_NAMES:
            self.assertNotIn("delete", name)
            self.assertNotIn("remove", name)

    def test_renaming_keeps_the_original_file(self):
        self.make_wiki(title="Alpha", slug="alpha")
        self.memory.rename_page(target="wiki/alpha", new_target="wiki/beta", actor="tester")
        self.assertTrue((self.vault_root / "wiki" / "alpha.md").is_file())
        self.assertTrue((self.vault_root / "wiki" / "beta.md").is_file())


class AtomicWriteTests(MemoryCase):
    def test_an_oversized_page_leaves_no_partial_file(self):
        from primee.skills.vault.storage import VaultStorage

        small = VaultStorage(self.vault_root, max_file_bytes=200)
        from primee.memory.service import MemoryService

        service = MemoryService(small, self.clock)
        with self.assertRaises(PrimeeError):
            service.create_raw(title="Big", body="x" * 5000, summary="Too big.", actor="tester")
        self.assertEqual([p for p in self.vault_files() if p.startswith("raw/")], [])

    def test_no_temporary_files_are_left_behind(self):
        self.make_raw()
        self.make_wiki()
        self.memory.rebuild_index(actor="tester")
        leftovers = [p.name for p in self.vault_root.rglob(".primee-*")]
        self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()
