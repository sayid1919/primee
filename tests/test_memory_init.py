"""Safe Vault initialization: unsafe paths, non-empty directories, dry run."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, PrimeeError
from primee.memory.initializer import (
    check_path_is_safe,
    initialize_vault,
    inspect_directory,
    primee_rules_document,
)
from primee.memory.schema import CHANGELOG_FILE, CONTENT_FOLDERS, INDEX_FILE, PRIMEE_FILE

from .support import fixed_clock


class InitCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primee-init-")
        self.tmp_path = Path(self._tmp.name)
        self.clock = fixed_clock()

    def tearDown(self):
        self._tmp.cleanup()

    def names(self, root: Path) -> list[str]:
        return sorted(p.name for p in root.iterdir())


class StructureTests(InitCase):
    def test_creates_the_three_content_folders(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        for folder in CONTENT_FOLDERS:
            self.assertTrue((root / folder).is_dir(), msg=folder)

    def test_creates_the_three_root_files(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        for name in (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE):
            self.assertTrue((root / name).is_file(), msg=name)

    def test_never_creates_a_claude_file(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        for name in self.names(root):
            self.assertNotIn("claude", name.lower())

    def test_the_rules_document_describes_the_model_and_its_limits(self):
        text = primee_rules_document("2026-08-27T08:00:00+03:30")
        for expected in ("raw/", "wiki/", "outputs/", "INDEX.md", "CHANGELOG.md"):
            self.assertIn(expected, text)
        self.assertIn("not encrypted", text)
        self.assertIn("never creates a `CLAUDE.md`", text)

    def test_initialization_is_idempotent(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        (root / "raw" / "keep.md").write_text("x", encoding="utf-8")
        plan = initialize_vault(root, self.clock)
        self.assertEqual(plan.created_files, [])
        self.assertEqual(plan.created_directories, [])
        self.assertTrue((root / "raw" / "keep.md").is_file())

    def test_index_and_changelog_start_valid(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        self.assertIn("# Vault index", (root / INDEX_FILE).read_text(encoding="utf-8"))
        self.assertIn("Append-only", (root / CHANGELOG_FILE).read_text(encoding="utf-8"))


class DryRunTests(InitCase):
    def test_dry_run_creates_nothing(self):
        root = self.tmp_path / "V"
        plan = initialize_vault(root, self.clock, dry_run=True)
        self.assertTrue(plan.dry_run)
        self.assertFalse(root.exists())
        self.assertEqual(plan.created_files, [])

    def test_dry_run_reports_exactly_what_it_would_create(self):
        plan = initialize_vault(self.tmp_path / "V", self.clock, dry_run=True)
        self.assertEqual(plan.directories, list(CONTENT_FOLDERS))
        self.assertEqual(plan.files, [INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE])
        self.assertIn("Would create", plan.describe())

    def test_dry_run_on_a_complete_vault_reports_nothing_to_do(self):
        root = self.tmp_path / "V"
        initialize_vault(root, self.clock)
        plan = initialize_vault(root, self.clock, dry_run=True)
        self.assertFalse(plan.creates_anything)
        self.assertIn("already complete", plan.describe())


class UnsafePathTests(InitCase):
    def test_refuses_a_relative_path(self):
        with self.assertRaises(PrimeeError) as ctx:
            check_path_is_safe("relative/vault")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_PATH_REJECTED)

    def test_refuses_an_empty_path(self):
        for bad in (None, "", "   "):
            with self.assertRaises(PrimeeError, msg=repr(bad)):
                check_path_is_safe(bad)

    def test_refuses_system_directories(self):
        for bad in ("/", "/etc", "/usr", "/bin", "/var", "/System"):
            with self.assertRaises(PrimeeError, msg=bad):
                check_path_is_safe(bad)

    def test_refuses_windows_system_directories(self):
        for bad in ("/C:/Windows", "/c/Windows/System32", "/d/Program Files"):
            with self.assertRaises(PrimeeError, msg=bad):
                check_path_is_safe(bad)

    def test_refuses_the_home_directory_itself(self):
        with self.assertRaises(PrimeeError):
            check_path_is_safe(str(Path.home()))

    def test_refuses_a_git_repository(self):
        root = self.tmp_path / "repo"
        (root / ".git").mkdir(parents=True)
        with self.assertRaises(PrimeeError) as ctx:
            check_path_is_safe(root)
        self.assertIn("version control", ctx.exception.message)

    def test_refuses_a_path_that_is_a_file(self):
        target = self.tmp_path / "afile"
        target.write_text("x", encoding="utf-8")
        with self.assertRaises(PrimeeError):
            inspect_directory(target)

    def test_no_personal_path_is_hardcoded_anywhere(self):
        from . import SRC

        source = (SRC / "primee" / "memory" / "initializer.py").read_text(encoding="utf-8")
        for forbidden in ("C:\\Users\\", "/home/", "Documents/PrimeeVault"):
            self.assertNotIn(forbidden, source, msg=forbidden)


class NonEmptyDirectoryTests(InitCase):
    def test_refuses_an_unrelated_non_empty_directory(self):
        root = self.tmp_path / "Photos"
        root.mkdir()
        (root / "holiday.jpg").write_text("not really an image", encoding="utf-8")
        (root / "taxes.pdf").write_text("not really a pdf", encoding="utf-8")
        with self.assertRaises(PrimeeError) as ctx:
            initialize_vault(root, self.clock)
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_PATH_REJECTED)
        self.assertIn("not empty", ctx.exception.message)
        self.assertEqual(self.names(root), ["holiday.jpg", "taxes.pdf"])

    def test_an_empty_directory_is_accepted(self):
        root = self.tmp_path / "Empty"
        root.mkdir()
        initialize_vault(root, self.clock)
        self.assertTrue((root / "raw").is_dir())

    def test_a_partial_vault_is_adopted_and_completed(self):
        root = self.tmp_path / "Half"
        (root / "raw").mkdir(parents=True)
        plan = initialize_vault(root, self.clock)
        self.assertIn("raw", plan.existing)
        self.assertIn("wiki", plan.created_directories)

    def test_an_unrelated_directory_can_be_adopted_only_on_purpose(self):
        root = self.tmp_path / "Mixed"
        root.mkdir()
        (root / "notes.txt").write_text("existing", encoding="utf-8")
        with self.assertRaises(PrimeeError):
            initialize_vault(root, self.clock)
        plan = initialize_vault(root, self.clock, adopt_non_empty=True)
        self.assertIn("raw", plan.created_directories)
        self.assertEqual((root / "notes.txt").read_text(encoding="utf-8"), "existing")

    def test_a_dot_file_alone_does_not_block_initialization(self):
        root = self.tmp_path / "WithGitkeep"
        root.mkdir()
        (root / ".gitkeep").write_text("", encoding="utf-8")
        initialize_vault(root, self.clock)
        self.assertTrue((root / "wiki").is_dir())


if __name__ == "__main__":
    unittest.main()
