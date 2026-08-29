"""Vault path isolation: traversal, absolute paths, symlinks and escapes."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, PathRejectedError, PrimeeError
from primee.core.paths import normalize_relative_path, resolve_within, sanitize_component
from primee.skills.vault.storage import VaultStorage

from .support import TempVaultCase


def symlinks_supported(directory: Path) -> bool:
    probe = directory / "__symlink_probe__"
    target = directory / "__symlink_target__"
    try:
        target.write_text("x", encoding="utf-8")
        probe.symlink_to(target)
    except (OSError, NotImplementedError, AttributeError):
        return False
    finally:
        for path in (probe, target):
            try:
                if path.is_symlink() or path.exists():
                    path.unlink()
            except OSError:
                pass
    return True


class NormalizationTests(unittest.TestCase):
    def test_accepts_ordinary_relative_paths(self):
        self.assertEqual(normalize_relative_path("plans/2026-08-27.md"), "plans/2026-08-27.md")
        self.assertEqual(normalize_relative_path("  notes/idea.md  "), "notes/idea.md")

    def test_accepts_persian_filenames(self):
        self.assertEqual(
            normalize_relative_path("یادداشت‌ها/امروز.md"), "یادداشت‌ها/امروز.md"
        )

    def test_rejects_parent_traversal(self):
        for bad in ["../secret.md", "plans/../../secret.md", "..", "a/../../b.md"]:
            with self.assertRaises(PathRejectedError, msg=bad):
                normalize_relative_path(bad)

    def test_rejects_absolute_paths(self):
        for bad in ["/etc/passwd", "C:/Windows/system.ini", "c:/x", "~/notes.md", "//server/share/x"]:
            with self.assertRaises(PathRejectedError, msg=bad):
                normalize_relative_path(bad)

    def test_rejects_backslash_separators(self):
        with self.assertRaises(PathRejectedError):
            normalize_relative_path("plans\\today.md")

    def test_rejects_windows_reserved_device_names(self):
        for bad in ["CON", "nul.md", "notes/COM1.txt", "LPT9.md", "aux.md"]:
            with self.assertRaises(PathRejectedError, msg=bad):
                normalize_relative_path(bad)

    def test_rejects_control_and_bidi_characters(self):
        for bad in ["a\x01b.md", "note\x00.md", "bad\u202egnp.md"]:
            with self.assertRaises(PathRejectedError, msg=repr(bad)):
                normalize_relative_path(bad)

    def test_rejects_trailing_dot_or_space_components(self):
        for bad in ["notes./x.md", "notes /x.md", "file."]:
            with self.assertRaises(PathRejectedError, msg=bad):
                normalize_relative_path(bad)

    def test_rejects_excessive_depth_and_length(self):
        with self.assertRaises(PathRejectedError):
            normalize_relative_path("/".join("abcdefghij"))
        with self.assertRaises(PathRejectedError):
            normalize_relative_path("x" * 500)

    def test_rejects_non_string_input(self):
        for bad in [None, 5, ["a.md"]]:
            with self.assertRaises(PathRejectedError):
                normalize_relative_path(bad)

    def test_sanitize_component_produces_a_safe_name(self):
        self.assertEqual(sanitize_component("my: note?"), "my-note")
        self.assertEqual(sanitize_component("../../etc"), "etc")
        self.assertEqual(sanitize_component("CON"), "untitled")
        self.assertEqual(sanitize_component("   "), "untitled")


class ResolutionTests(TempVaultCase):
    def test_resolves_inside_the_root(self):
        target = resolve_within(self.vault_root, "plans/today.md")
        self.assertTrue(str(target).startswith(str(self.vault_root.resolve())))

    def test_refuses_to_target_the_root_itself(self):
        (self.vault_root / "sub").mkdir()
        with self.assertRaises(PathRejectedError):
            resolve_within(self.vault_root, "sub/..")

    @unittest.skipUnless(
        symlinks_supported(Path(os.environ.get("TMPDIR", "/tmp"))),
        "This platform or account cannot create symbolic links.",
    )
    def test_refuses_to_follow_a_symlinked_directory(self):
        (self.outside / "loot").mkdir()
        (self.outside / "loot" / "secret.md").write_text("synthetic", encoding="utf-8")
        (self.vault_root / "escape").symlink_to(self.outside / "loot", target_is_directory=True)
        with self.assertRaises(PathRejectedError) as ctx:
            resolve_within(self.vault_root, "escape/secret.md")
        self.assertIn("symbolic link", ctx.exception.message)

    @unittest.skipUnless(
        symlinks_supported(Path(os.environ.get("TMPDIR", "/tmp"))),
        "This platform or account cannot create symbolic links.",
    )
    def test_refuses_to_follow_a_symlinked_file(self):
        secret = self.outside / "secret.md"
        secret.write_text("synthetic", encoding="utf-8")
        (self.vault_root / "link.md").symlink_to(secret)
        with self.assertRaises(PathRejectedError):
            resolve_within(self.vault_root, "link.md")


class StorageIsolationTests(TempVaultCase):
    def setUp(self):
        super().setUp()
        self.storage = VaultStorage(self.vault_root)

    def test_unconfigured_root_is_reported(self):
        with self.assertRaises(PrimeeError) as ctx:
            VaultStorage("")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_NOT_CONFIGURED)

    def test_missing_root_directory_is_reported(self):
        with self.assertRaises(PrimeeError) as ctx:
            VaultStorage(self.tmp_path / "nope")
        self.assertEqual(ctx.exception.code, ErrorCode.VAULT_PATH_REJECTED)

    def test_traversal_write_is_blocked_and_nothing_is_created(self):
        with self.assertRaises(PathRejectedError):
            self.storage.create("../escaped.md", "content")
        self.assertFalse((self.tmp_path / "escaped.md").exists())

    def test_absolute_write_is_blocked_and_nothing_is_created(self):
        target = self.outside / "escaped.md"
        with self.assertRaises(PathRejectedError):
            self.storage.create(str(target), "content")
        self.assertFalse(target.exists())

    def test_traversal_read_is_blocked(self):
        (self.outside / "secret.md").write_text("synthetic", encoding="utf-8")
        with self.assertRaises(PathRejectedError):
            self.storage.read("../outside/secret.md")

    @unittest.skipUnless(
        symlinks_supported(Path(os.environ.get("TMPDIR", "/tmp"))),
        "This platform or account cannot create symbolic links.",
    )
    def test_symlink_escape_write_is_blocked_and_target_untouched(self):
        (self.outside / "loot").mkdir()
        victim = self.outside / "loot" / "victim.md"
        victim.write_text("original", encoding="utf-8")
        (self.vault_root / "escape").symlink_to(self.outside / "loot", target_is_directory=True)
        with self.assertRaises(PathRejectedError):
            self.storage.update("escape/victim.md", "overwritten")
        self.assertEqual(victim.read_text(encoding="utf-8"), "original")

    def test_listing_never_leaves_the_root(self):
        (self.vault_root / "plans").mkdir()
        (self.vault_root / "plans" / "a.md").write_text("a", encoding="utf-8")
        (self.outside / "b.md").write_text("b", encoding="utf-8")
        paths = [entry.path for entry in self.storage.list()]
        self.assertEqual(paths, ["plans/a.md"])


if __name__ == "__main__":
    unittest.main()
