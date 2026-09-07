"""The Haaniye profile records facts, quotes sources and refuses to guess."""

from __future__ import annotations

import json
import unittest

from primee.core.permissions import PERMISSIONS, SKILL_REQUESTABLE, PermissionEngine, PermissionPolicy
from primee.voice.profiles import HAANIYE, get_profile


class HaaniyeProfileTests(unittest.TestCase):
    def test_basic_metadata(self):
        self.assertIs(get_profile("haaniye"), HAANIYE)
        self.assertIsNone(get_profile("nope"))
        self.assertEqual(HAANIYE.language, "fa")
        self.assertEqual(HAANIYE.engine, "sherpa-onnx")
        self.assertEqual(HAANIYE.model_type, "vits-mimic3")
        self.assertEqual(HAANIYE.quality, "low")
        self.assertEqual(HAANIYE.speakers, 1)
        self.assertEqual(HAANIYE.model_repository, "csukuangfj/vits-mimic3-fa-haaniye_low")
        self.assertEqual(HAANIYE.model_files, ("fa-haaniye_low.onnx", "fa-haaniye_low.onnx.json", "tokens.txt"))
        self.assertEqual(HAANIYE.data_dir, "espeak-ng-data")

    def test_cc0_voice_licence_record_points_at_the_official_file(self):
        record = HAANIYE.license_for("voice")
        self.assertEqual(record.license, "CC0")
        self.assertEqual(
            record.source_url,
            "https://github.com/MycroftAI/mimic3-voices/blob/master/voices/fa/haaniye_low/LICENSE",
        )
        self.assertIn("CC-0", record.statement)

    def test_licences_are_reported_separately(self):
        subjects = [record.subject for record in HAANIYE.licenses]
        self.assertEqual(subjects, ["voice", "dataset", "converted-model-repository", "engine", "bundled-libraries"])
        self.assertIsNone(HAANIYE.license_for("dataset").license)
        self.assertIn("public domain", HAANIYE.license_for("dataset").statement)
        self.assertIsNone(HAANIYE.license_for("converted-model-repository").license)
        self.assertEqual(HAANIYE.license_for("engine").license, "Apache-2.0")
        self.assertIsNone(HAANIYE.license_for("bundled-libraries").license)

    def test_source_tbd_is_a_standing_provenance_warning(self):
        self.assertEqual(HAANIYE.provenance_source_file, "TBD")
        warnings = HAANIYE.provenance_warnings()
        self.assertTrue(any("TBD" in warning for warning in warnings))
        self.assertTrue(any("benchmark only" in warning for warning in warnings))

    def test_no_claim_about_gender_or_redistribution(self):
        self.assertIn("not stated", HAANIYE.speaker_gender)
        self.assertIn("listening", HAANIYE.speaker_gender)
        self.assertIn("not assessed", HAANIYE.redistribution_status)
        self.assertEqual(HAANIYE.approved_use, "private local benchmark only")
        described = json.dumps(HAANIYE.describe(), ensure_ascii=False).lower()
        for forbidden in ("female voice", "commercially cleared", "is cleared for redistribution", "legally cleared", "may be redistributed"):
            self.assertNotIn(forbidden, described)


class PlaybackPermissionTests(unittest.TestCase):
    def test_audio_playback_exists_and_is_not_skill_requestable(self):
        spec = PERMISSIONS["audio.playback"]
        self.assertTrue(spec.implemented)
        self.assertFalse(spec.skill_requestable)
        self.assertNotIn("audio.playback", SKILL_REQUESTABLE)

    def test_audio_capture_stays_reserved(self):
        self.assertFalse(PERMISSIONS["audio.capture"].implemented)
        engine = PermissionEngine(PermissionPolicy.from_mapping({"modes": {"audio.capture": "auto"}}))
        self.assertFalse(engine.evaluate(frozenset({"audio.capture"}), "audio.capture").allowed)

    def test_playback_is_never_by_default(self):
        self.assertEqual(PermissionPolicy().mode_for("audio.playback"), "never")


if __name__ == "__main__":
    unittest.main()
