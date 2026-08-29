"""Skill discovery, manifest validation and safe handler binding."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode, ManifestError, PrimeeError
from primee.core.manifest import build_manifest
from primee.core.skill_loader import discover_skills, read_skill_file

from .support import BROKEN_SKILLS, BUNDLED_SKILLS, VALID_SKILLS

BASE_METADATA = {
    "name": "demo",
    "version": "1.0.0",
    "description": "A demo skill.",
    "triggers": ["demo"],
    "exclusions": [],
    "required_permissions": [],
    "inputs": [],
    "outputs": [],
    "persistence": {"vault_writes": False},
    "handler": "handler.py:run",
}


class DiscoveryTests(unittest.TestCase):
    def test_discovers_the_five_bundled_skills(self):
        registry = discover_skills(BUNDLED_SKILLS)
        self.assertEqual(registry.names(), ["inbox", "metrics", "plan", "trends", "vault"])
        self.assertEqual(registry.errors, [])

    def test_every_bundled_skill_has_a_complete_manifest(self):
        for skill in discover_skills(BUNDLED_SKILLS):
            manifest = skill.manifest
            self.assertTrue(manifest.triggers)
            self.assertTrue(manifest.description)
            self.assertTrue(manifest.handler.endswith(":run"))
            self.assertTrue(skill.body.strip())

    def test_discovers_valid_fixture_skills(self):
        registry = discover_skills(VALID_SKILLS)
        self.assertIn("alpha", registry.skills)
        self.assertIn("beta", registry.skills)

    def test_missing_root_is_reported_not_raised(self):
        registry = discover_skills(BUNDLED_SKILLS.parent / "does-not-exist")
        self.assertEqual(len(registry.skills), 0)
        self.assertEqual(registry.errors[0].error_code, ErrorCode.SKILLS_ROOT_MISSING)


class DuplicateNameTests(unittest.TestCase):
    def test_duplicate_skill_name_is_rejected_and_reported(self):
        registry = discover_skills(VALID_SKILLS)
        duplicates = [e for e in registry.errors if e.error_code == ErrorCode.DUPLICATE_SKILL_NAME]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0].skill_name, "alpha")
        # The first directory in sorted order wins; the duplicate never loads.
        self.assertEqual(registry.skills["alpha"].directory.name, "alpha")
        self.assertEqual(registry.skills["alpha"].manifest.version, "1.0.0")


class MalformedSkillTests(unittest.TestCase):
    def load(self, name: str):
        return read_skill_file(BROKEN_SKILLS / name)

    def test_missing_frontmatter_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("no_frontmatter")
        self.assertEqual(ctx.exception.code, ErrorCode.FRONTMATTER_INVALID)

    def test_malformed_yaml_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("bad_yaml")
        self.assertEqual(ctx.exception.code, ErrorCode.FRONTMATTER_INVALID)

    def test_dangerous_yaml_tag_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("dangerous_yaml")
        self.assertEqual(ctx.exception.code, ErrorCode.FRONTMATTER_INVALID)

    def test_missing_required_field_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("missing_field")
        self.assertEqual(ctx.exception.code, ErrorCode.MANIFEST_INVALID)
        self.assertIn("handler", ctx.exception.message)

    def test_unknown_permission_is_rejected(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("bad_permission")
        self.assertEqual(ctx.exception.code, ErrorCode.MANIFEST_INVALID)

    def test_handler_path_escape_is_rejected_at_manifest_level(self):
        with self.assertRaises(PrimeeError) as ctx:
            self.load("unsafe_handler")
        self.assertEqual(ctx.exception.code, ErrorCode.MANIFEST_INVALID)

    def test_every_broken_fixture_fails_to_load(self):
        registry = discover_skills(BROKEN_SKILLS)
        self.assertEqual(len(registry.skills), 0)
        self.assertEqual(len(registry.errors), 6)


class ManifestFieldTests(unittest.TestCase):
    def build(self, **overrides):
        metadata = dict(BASE_METADATA)
        metadata.update(overrides)
        return build_manifest(metadata)

    def test_each_required_field_is_enforced(self):
        for field in BASE_METADATA:
            metadata = dict(BASE_METADATA)
            metadata.pop(field)
            with self.assertRaises(ManifestError, msg=f"{field} should be required"):
                build_manifest(metadata)

    def test_rejects_invalid_name(self):
        for bad in ["Alpha", "1alpha", "al-pha", "", "a" * 40]:
            with self.assertRaises(ManifestError):
                self.build(name=bad)

    def test_rejects_invalid_version(self):
        for bad in ["1.0", "v1.0.0", "latest"]:
            with self.assertRaises(ManifestError):
                self.build(version=bad)

    def test_rejects_empty_trigger_list(self):
        with self.assertRaises(ManifestError):
            self.build(triggers=[])

    def test_rejects_duplicate_trigger_phrases(self):
        with self.assertRaises(ManifestError):
            self.build(triggers=["demo", "demo"])

    def test_rejects_core_only_permission(self):
        with self.assertRaises(ManifestError):
            self.build(required_permissions=["audit.write"])

    def test_rejects_handler_outside_skill_directory(self):
        for bad in ["../other.py:run", "/etc/passwd:run", "handler.py", "handler.py:", "pkg.mod:run"]:
            with self.assertRaises(ManifestError):
                self.build(handler=bad)

    def test_persistence_must_match_declared_permissions(self):
        with self.assertRaises(ManifestError):
            self.build(persistence={"vault_writes": True}, required_permissions=[])
        with self.assertRaises(ManifestError):
            self.build(
                persistence={"vault_writes": False},
                required_permissions=["vault.create"],
            )

    def test_unknown_io_type_is_rejected(self):
        with self.assertRaises(ManifestError):
            self.build(inputs=[{"name": "x", "type": "executable"}])


class HandlerBindingTests(unittest.TestCase):
    def test_handler_loads_lazily_and_only_on_demand(self):
        registry = discover_skills(VALID_SKILLS)
        skill = registry.require("alpha")
        self.assertIsNone(skill._handler)
        handler = skill.load_handler()
        self.assertTrue(callable(handler))
        self.assertIs(handler, skill.load_handler())

    def test_missing_handler_file_is_reported_safely(self):
        registry = discover_skills(BUNDLED_SKILLS)
        skill = registry.require("plan")
        object.__setattr__(skill.manifest, "handler_file", "not_here.py")
        with self.assertRaises(PrimeeError) as ctx:
            skill.handler_path()
        self.assertEqual(ctx.exception.code, ErrorCode.HANDLER_INVALID)


if __name__ == "__main__":
    unittest.main()
