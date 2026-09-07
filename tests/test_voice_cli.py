"""`primee voice` and the voice section of `primee doctor`."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from primee.cli import main

from . import REPO_ROOT
from .voice_support import VoiceCase

CONFIG = str(REPO_ROOT / "config")


def run_cli(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


class VoiceCommandTests(VoiceCase):
    def test_bare_voice_returns_a_controlled_notice_not_a_hosted_call(self):
        code, output = run_cli(["--config", CONFIG, "voice", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertIn("not implemented", payload["notice"])
        self.assertEqual(payload["stage"], "tts-benchmark-preparation")
        self.assertFalse(payload["enabled"])

    def test_status_with_a_plain_checkout_is_text_only(self):
        code, output = run_cli(["--config", CONFIG, "voice", "status", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertFalse(payload["adapter"]["available"])
        self.assertEqual(payload["permission"]["mode"], "never")
        self.assertEqual(payload["network"], "none: no voice code opens a socket")

    def test_profile_prints_licences_and_warnings(self):
        code, output = run_cli(["voice", "profile", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["key"], "haaniye")
        self.assertEqual(payload["provenance_source_file"], "TBD")
        code, text = run_cli(["voice", "profile"])
        self.assertEqual(code, 0)
        self.assertIn("CC0", text)
        self.assertIn("TBD", text)
        self.assertIn("listening", text)

    def test_speak_without_a_runtime_shows_the_text_and_exits_non_zero(self):
        code, output = run_cli(["--config", CONFIG, "voice", "speak", "سلام پرایمی", "--json", "--no-audit"])
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertFalse(payload["spoken"])
        self.assertEqual(payload["text"], "سلام پرایمی")
        self.assertIn(payload["error_code"], ("PERMISSION_DENIED", "VOICE_DISABLED"))

    def test_benchmark_refuses_a_directory_inside_the_repository(self):
        code, output = run_cli(
            ["--config", CONFIG, "voice", "benchmark", "--output", str(REPO_ROOT / "benchmarks"), "--json", "--no-audit"]
        )
        self.assertEqual(code, 1)
        payload = json.loads(output)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["cases"], [])

    def test_doctor_reports_the_voice_state(self):
        code, output = run_cli(["doctor", "--config", CONFIG, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["hosted_ai_dependencies"], [])
        self.assertFalse(payload["voice"]["enabled"])
        self.assertIn("audio.playback", payload["permission_policy"]["modes"])

    def test_permissions_command_lists_audio_playback_as_core_only(self):
        code, output = run_cli(["permissions", "--json"])
        self.assertEqual(code, 0)
        rows = {row["name"]: row for row in json.loads(output)}
        self.assertTrue(rows["audio.playback"]["implemented"])
        self.assertFalse(rows["audio.playback"]["skill_requestable"])
        self.assertFalse(rows["audio.capture"]["implemented"])


if __name__ == "__main__":
    unittest.main()
