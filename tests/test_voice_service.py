"""VoiceService decisions, the spoken summary, temp audio, transcripts, benchmark."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, VoiceError
from primee.core.result_types import SkillResult
from primee.voice.benchmark import BENCHMARK_PHRASES, check_output_dir, run_benchmark
from primee.voice.service import VoiceService
from primee.voice.session import TranscriptBuffer
from primee.voice.summarize import shorten_for_speech, spoken_summary
from primee.voice.tempaudio import TemporaryWav, inspect_wav

from . import REPO_ROOT
from .support import fixed_clock
from .voice_support import VoiceCase, make_fake_model, memory_audit, voice_config


class RecordingPlayer:
    name = "recording"

    def __init__(self, available: bool = True) -> None:
        self._available = available
        self.played: list[Path] = []
        self.existed_at_play: list[bool] = []

    def available(self) -> bool:
        return self._available

    def play(self, path: Path) -> None:
        self.existed_at_play.append(Path(path).is_file())
        self.played.append(Path(path))


class ServiceCase(VoiceCase):
    def service(self, *, playback_mode="auto", enabled=True, engine="sherpa-onnx", model=True, behaviour="", player=None):
        if model:
            make_fake_model(self.models_dir, behaviour=behaviour)
        config = voice_config(
            enabled=enabled, engine=engine, models_dir=self.models_dir, worker=self.worker,
            playback_mode=playback_mode, state_dir=self.state_dir,
        )
        self.audit = memory_audit()
        self.player = player if player is not None else RecordingPlayer()
        return VoiceService(config, audit=self.audit, clock=fixed_clock(), player=self.player, temp_dir=self.state_dir / "tmp")


class FallbackTests(ServiceCase):
    def test_disabled_configuration_means_text_only(self):
        service = self.service(enabled=False)
        outcome = service.speak("سلام")
        self.assertFalse(outcome.spoken)
        self.assertEqual(outcome.engine, "text-only")
        self.assertEqual(outcome.error_code, ErrorCode.VOICE_DISABLED)
        self.assertEqual(outcome.text, "سلام")
        self.assertEqual(self.audit.events[-1].outcome, "text_only")

    def test_engine_none_means_text_only(self):
        service = self.service(engine="none", model=False)
        self.assertEqual(service.speak("x").error_code, ErrorCode.VOICE_DISABLED)

    def test_permission_never_means_text_only_even_when_everything_is_installed(self):
        service = self.service(playback_mode="never")
        outcome = service.speak("سلام")
        self.assertFalse(outcome.spoken)
        self.assertEqual(outcome.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(self.player.played, [])

    def test_permission_approval_needs_an_explicit_approval(self):
        service = self.service(playback_mode="approval")
        self.assertEqual(service.speak("سلام").error_code, ErrorCode.APPROVAL_REQUIRED)
        approved = service.speak("سلام", approved=True)
        self.assertTrue(approved.spoken)
        self.assertEqual(self.audit.events[-1].approval_state, "granted")

    def test_missing_model_falls_back_to_text(self):
        service = self.service(model=False)
        outcome = service.speak("سلام")
        self.assertFalse(outcome.spoken)
        self.assertEqual(outcome.error_code, ErrorCode.VOICE_MODEL_MISSING)
        self.assertEqual(outcome.text, "سلام")

    def test_worker_failure_falls_back_to_text_and_keeps_no_file(self):
        service = self.service(behaviour="nonzero")
        outcome = service.speak("سلام")
        self.assertFalse(outcome.spoken)
        self.assertEqual(outcome.error_code, ErrorCode.VOICE_PROCESS_FAILED)
        self.assertEqual(list((self.state_dir / "tmp").rglob("*.wav")), [])

    def test_playback_unavailable_keeps_the_text_visible(self):
        service = self.service(player=RecordingPlayer(available=False))
        outcome = service.speak("سلام")
        self.assertTrue(outcome.synthesized)
        self.assertFalse(outcome.spoken)
        self.assertEqual(outcome.error_code, ErrorCode.VOICE_PLAYBACK_UNAVAILABLE)
        self.assertEqual(outcome.text, "سلام")


class SpeakingTests(ServiceCase):
    def test_speaks_then_deletes_the_temporary_wav(self):
        service = self.service()
        outcome = service.speak("برنامه امروز شما آماده است.")
        self.assertTrue(outcome.spoken)
        self.assertEqual(len(self.player.played), 1)
        self.assertEqual(self.player.existed_at_play, [True])
        self.assertFalse(self.player.played[0].exists())
        self.assertEqual(list((self.state_dir / "tmp").rglob("*")), [])

    def test_audit_records_counts_never_words(self):
        service = self.service()
        service.speak("متن کاملاً خصوصی")
        event = self.audit.events[-1]
        self.assertEqual(event.actor_skill, "core.voice")
        self.assertEqual(event.permission, "audio.playback")
        self.assertEqual(event.outcome, "spoken")
        serialised = json.dumps(event.to_dict(), ensure_ascii=False)
        self.assertNotIn("خصوصی", serialised)
        self.assertEqual(event.detail["characters"], len("متن کاملاً خصوصی"))

    def test_only_a_short_summary_is_spoken(self):
        service = self.service()
        long_text = "جمله اول است. " * 40
        outcome = service.speak(long_text)
        self.assertLessEqual(len(outcome.text), 240)
        self.assertTrue(outcome.text.endswith("."))

    def test_speak_result_uses_the_summary_not_the_data(self):
        service = self.service()
        result = SkillResult.ok("metrics", "Website visits: 120 today.", structured_data={"secret_list": ["a", "b"]})
        outcome = service.speak_result(result)
        self.assertEqual(outcome.text, "Website visits: 120 today.")
        self.assertNotIn("secret_list", outcome.text)

    def test_status_is_json_safe_and_honest(self):
        service = self.service()
        status = service.status()
        json.dumps(status, ensure_ascii=False)
        self.assertTrue(status["adapter"]["available"])
        self.assertIn("push-to-talk", status["implemented"])
        self.assertEqual(status["permission"]["mode"], "auto")


class SummaryTests(unittest.TestCase):
    def test_strips_markdown_and_collapses_whitespace(self):
        self.assertEqual(shorten_for_speech("**Plan**  for\n\ntoday: `x`"), "Plan for today: x")

    def test_keeps_persian_zwnj(self):
        text = "می‌توانم"
        self.assertEqual(shorten_for_speech(text), text)

    def test_cuts_at_sentence_boundaries(self):
        text = "اول. دوم؟ سوم! چهارم."
        self.assertEqual(shorten_for_speech(text, max_chars=10), "اول. دوم؟")

    def test_single_long_sentence_is_cut_at_a_word(self):
        text = "کلمه " * 100
        spoken = shorten_for_speech(text, max_chars=30)
        self.assertLessEqual(len(spoken), 31)
        self.assertTrue(spoken.endswith("…"))

    def test_failed_result_speaks_the_sanitized_error(self):
        result = SkillResult.fail("plan", "Failed", ErrorCode.CONNECTOR_ERROR, "Connector unavailable.")
        self.assertEqual(spoken_summary(result), "Connector unavailable.")

    def test_empty_input(self):
        self.assertEqual(shorten_for_speech(""), "")
        self.assertEqual(shorten_for_speech("   \n "), "")


class TemporaryWavTests(VoiceCase):
    def test_deleted_on_success_and_on_error(self):
        with TemporaryWav(self.tmp_path) as path:
            path.write_bytes(b"RIFF")
            kept = path
        self.assertFalse(kept.exists())
        self.assertFalse(kept.parent.exists())
        try:
            with TemporaryWav(self.tmp_path) as path:
                path.write_bytes(b"RIFF")
                kept = path
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertFalse(kept.exists())

    def test_keep_flag_preserves_the_file(self):
        with TemporaryWav(self.tmp_path, keep=True) as path:
            path.write_bytes(b"RIFF")
        self.assertTrue(path.exists())

    def test_inspect_rejects_non_wav_and_empty_files(self):
        bad = self.tmp_path / "bad.wav"
        bad.write_bytes(b"not a wav")
        with self.assertRaises(VoiceError) as caught:
            inspect_wav(bad)
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_OUTPUT_INVALID)
        empty = self.tmp_path / "empty.wav"
        empty.write_bytes(b"")
        with self.assertRaises(VoiceError):
            inspect_wav(empty)
        with self.assertRaises(VoiceError):
            inspect_wav(self.tmp_path / "missing.wav")


class TranscriptPolicyTests(unittest.TestCase):
    def test_transcripts_are_not_persisted_by_default(self):
        buffer = TranscriptBuffer()
        buffer.add("گفتار من")
        written: list[str] = []
        with self.assertRaises(VoiceError) as caught:
            buffer.persist(written.append)
        self.assertEqual(caught.exception.code, ErrorCode.PERMISSION_DENIED)
        with self.assertRaises(VoiceError):
            buffer.persist(written.append, approved=True, approval_note="x")
        self.assertEqual(written, [])
        self.assertNotIn("گفتار", json.dumps(buffer.describe(), ensure_ascii=False))

    def test_persistence_needs_config_and_explicit_approval_with_a_note(self):
        buffer = TranscriptBuffer(allow_persistence=True)
        buffer.add("a")
        buffer.add("b")
        written: list[str] = []
        with self.assertRaises(VoiceError) as caught:
            buffer.persist(written.append)
        self.assertEqual(caught.exception.code, ErrorCode.APPROVAL_REQUIRED)
        with self.assertRaises(VoiceError):
            buffer.persist(written.append, approved=True)
        self.assertEqual(buffer.persist(written.append, approved=True, approval_note="keep for review"), 2)
        self.assertEqual(written, ["a", "b"])
        buffer.clear()
        self.assertEqual(len(buffer), 0)


class BenchmarkTests(ServiceCase):
    def test_the_phrase_set_is_fixed_and_complete(self):
        self.assertEqual(len(BENCHMARK_PHRASES), 7)
        keys = [key for key, _, _ in BENCHMARK_PHRASES]
        self.assertEqual(keys, sorted(keys))
        texts = [text for _, _, text in BENCHMARK_PHRASES]
        self.assertTrue(any("website metrics" in text for text in texts))
        self.assertTrue(any("؟" in text for text in texts))

    def test_refuses_an_output_directory_inside_the_repository(self):
        with self.assertRaises(VoiceError):
            check_output_dir(REPO_ROOT / "benchmarks")
        with self.assertRaises(VoiceError):
            check_output_dir(Path("relative"))
        service = self.service()
        report = run_benchmark(service, REPO_ROOT / "benchmarks", clock=fixed_clock())
        self.assertFalse(report.ok)
        self.assertEqual(report.error_code, ErrorCode.INVALID_INPUT)

    def test_refuses_when_the_engine_is_unavailable(self):
        service = self.service(model=False)
        report = run_benchmark(service, self.outside, clock=fixed_clock())
        self.assertFalse(report.ok)
        self.assertEqual(report.error_code, ErrorCode.VOICE_MODEL_MISSING)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_writes_seven_wavs_and_a_report_and_plays_nothing(self):
        service = self.service()
        report = run_benchmark(service, self.outside, clock=fixed_clock())
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(len(report.cases), 7)
        self.assertEqual(self.player.played, [])
        wavs = sorted(p.name for p in report.output_dir.glob("*.wav"))
        self.assertEqual(len(wavs), 7)
        payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "primee-voice-benchmark")
        for case in payload["cases"]:
            self.assertIsNone(case["listening"]["classification"])
            self.assertIn("real_time_factor", case["metrics"])
        self.assertTrue(any("TBD" in warning for warning in payload["warnings"]))


if __name__ == "__main__":
    unittest.main()
