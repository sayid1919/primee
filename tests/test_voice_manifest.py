"""Manifest parsing and byte-for-byte verification of installed files."""

from __future__ import annotations

import copy
import hashlib
import json
import unittest

from primee.core.errors import ErrorCode, VoiceError
from primee.voice.manifest import (
    git_blob_sha1_of,
    load_manifest,
    parse_manifest,
    require_verified,
    sha256_of,
    verify_component,
)

from . import FIXTURES
from .voice_support import VoiceCase, make_fake_model

FIXTURE = FIXTURES / "voice" / "haaniye-sherpa.manifest.json"


def fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class ParsingTests(unittest.TestCase):
    def test_the_fixture_parses_and_separates_hash_kinds(self):
        manifest = load_manifest(FIXTURE)
        self.assertEqual(manifest.profile, "haaniye")
        model = manifest.model
        self.assertIsNotNone(model)
        self.assertEqual(model.revision, "0123456789abcdef0123456789abcdef01234567")
        self.assertEqual(model.file("fa-haaniye_low.onnx").hash_type, "publisher-sha256")
        self.assertEqual(model.file("tokens.txt").hash_type, "publisher-git-blob-sha1")
        self.assertEqual(model.file("espeak-ng-data/phontab").hash_type, "publisher-git-blob-sha1")
        self.assertEqual([r.name for r in manifest.runtimes()], ["sherpa-onnx", "sherpa-onnx-core"])
        self.assertEqual(manifest.runtimes()[0].requires, ("sherpa-onnx-core==1.13.7",))

    def test_the_converted_repository_licence_stays_null(self):
        manifest = load_manifest(FIXTURE)
        self.assertIsNone(manifest.model.license)
        provenance = manifest.component("provenance")
        self.assertEqual(provenance.license, "CC0")
        self.assertEqual(provenance.extra["documents"]["SOURCE"].strip(), "TBD")

    def test_rejects_the_wrong_schema(self):
        raw = fixture()
        raw["schema"] = "something-else"
        with self.assertRaises(VoiceError) as caught:
            parse_manifest(raw)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_MANIFEST_INVALID)

    def test_rejects_a_model_without_a_full_revision(self):
        raw = fixture()
        raw["components"][2]["revision"] = "main"
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw["components"][2]["revision"] = None
        with self.assertRaises(VoiceError):
            parse_manifest(raw)

    def test_rejects_a_runtime_without_an_exact_version(self):
        raw = fixture()
        raw["components"][0]["version"] = None
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw["components"][0]["version"] = "latest"
        with self.assertRaises(VoiceError):
            parse_manifest(raw)

    def test_rejects_malformed_hashes_and_unknown_hash_types(self):
        raw = fixture()
        raw["components"][2]["files"][0]["hash"] = "abc"
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw = fixture()
        raw["components"][2]["files"][0]["hash_type"] = "md5"
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw = fixture()
        raw["components"][2]["files"][1]["hash_type"] = "publisher-sha256"  # 40 hex for a 64 hex type
        with self.assertRaises(VoiceError):
            parse_manifest(raw)

    def test_rejects_unsafe_paths_insecure_urls_and_duplicates(self):
        for bad_path in ("../escape", "/abs", "C:/x", "a//b", "espeak-ng-data/../x"):
            raw = fixture()
            raw["components"][2]["files"][0]["path"] = bad_path
            with self.assertRaises(VoiceError, msg=bad_path):
                parse_manifest(raw)
        raw = fixture()
        raw["components"][2]["files"][0]["url"] = "http://huggingface.co/insecure"
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw = fixture()
        raw["components"][2]["files"].append(copy.deepcopy(raw["components"][2]["files"][0]))
        with self.assertRaises(VoiceError):
            parse_manifest(raw)

    def test_requires_exactly_one_model_and_a_runtime(self):
        raw = fixture()
        raw["components"] = [c for c in raw["components"] if c["component"] != "model"]
        with self.assertRaises(VoiceError):
            parse_manifest(raw)
        raw = fixture()
        raw["components"] = [c for c in raw["components"] if c["component"] != "runtime"]
        with self.assertRaises(VoiceError):
            parse_manifest(raw)

    def test_missing_or_unreadable_file(self):
        with self.assertRaises(VoiceError) as caught:
            load_manifest(FIXTURE.parent / "does-not-exist.json")
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_MANIFEST_INVALID)


class HashTests(VoiceCase):
    def test_git_blob_sha1_matches_the_git_definition(self):
        path = self.tmp_path / "blob.txt"
        content = b"hello primee\n"
        path.write_bytes(content)
        expected = hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()
        self.assertEqual(git_blob_sha1_of(path), expected)
        # Known value: git hash-object of "hello\n" is ce013625030ba8dba906f756967f9e9ca394464a
        (self.tmp_path / "hello").write_bytes(b"hello\n")
        self.assertEqual(git_blob_sha1_of(self.tmp_path / "hello"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_sha256_of_a_file(self):
        path = self.tmp_path / "x.bin"
        path.write_bytes(b"abc")
        self.assertEqual(sha256_of(path), hashlib.sha256(b"abc").hexdigest())


class VerificationTests(VoiceCase):
    def test_a_correct_installation_verifies(self):
        model_dir = make_fake_model(self.models_dir)
        manifest = load_manifest(model_dir / "primee-manifest.json")
        report = verify_component(manifest.model, model_dir)
        self.assertTrue(report.ok, report.describe())
        self.assertEqual(report.describe()["states"], {"verified": 4})
        require_verified(report)

    def test_a_tampered_file_is_a_hash_mismatch(self):
        model_dir = make_fake_model(self.models_dir, tamper=True)
        manifest = load_manifest(model_dir / "primee-manifest.json")
        report = verify_component(manifest.model, model_dir)
        self.assertFalse(report.ok)
        states = {check.path: check.state for check in report.checks}
        self.assertEqual(states["tokens.txt"], "size_mismatch")
        with self.assertRaises(VoiceError) as caught:
            require_verified(report)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_HASH_MISMATCH)

    def test_same_size_different_bytes_is_still_a_mismatch(self):
        model_dir = make_fake_model(self.models_dir)
        path = model_dir / "tokens.txt"
        original = path.read_bytes()
        path.write_bytes(b"X" + original[1:])
        manifest = load_manifest(model_dir / "primee-manifest.json")
        report = verify_component(manifest.model, model_dir)
        self.assertEqual({c.path: c.state for c in report.checks}["tokens.txt"], "mismatch")
        with self.assertRaises(VoiceError) as caught:
            require_verified(report)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_HASH_MISMATCH)

    def test_missing_files_are_reported_as_missing_model(self):
        model_dir = make_fake_model(self.models_dir)
        (model_dir / "fa-haaniye_low.onnx").unlink()
        (model_dir / "tokens.txt").unlink()
        manifest = load_manifest(model_dir / "primee-manifest.json")
        report = verify_component(manifest.model, model_dir)
        with self.assertRaises(VoiceError) as caught:
            require_verified(report)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_MODEL_MISSING)

    def test_a_manifest_with_only_unverifiable_files_is_refused(self):
        model_dir = make_fake_model(self.models_dir)
        raw = json.loads((model_dir / "primee-manifest.json").read_text(encoding="utf-8"))
        for entry in raw["components"][1]["files"]:
            entry["hash_type"] = "none"
            entry["hash"] = ""
        manifest = parse_manifest(raw)
        with self.assertRaises(VoiceError) as caught:
            require_verified(verify_component(manifest.model, model_dir))
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_MANIFEST_INVALID)


if __name__ == "__main__":
    unittest.main()
