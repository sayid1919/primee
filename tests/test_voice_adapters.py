"""The sherpa adapter through the process boundary, with a stand-in worker."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from primee.core.errors import ErrorCode, VoiceError
from primee.voice.adapters.sherpa import SherpaTtsAdapter, default_worker_script
from primee.voice.adapters.text_only import TextOnlyFallback
from primee.voice.interfaces import SpeechRequest
from primee.voice.profiles import HAANIYE

from .voice_support import VoiceCase, interpreter_runner, make_fake_model, voice_config


class TextOnlyTests(unittest.TestCase):
    def test_is_never_available_and_never_speaks(self):
        adapter = TextOnlyFallback("because")
        self.assertFalse(adapter.availability().available)
        self.assertEqual(adapter.availability().error_code, ErrorCode.VOICE_DISABLED)
        with self.assertRaises(VoiceError):
            adapter.synthesize(SpeechRequest(text="x", output_path=Path("/tmp/never.wav")))


class SherpaAdapterTests(VoiceCase):
    def adapter(self, *, behaviour: str = "", tamper: bool = False, worker: Path | None = None, model: bool = True) -> SherpaTtsAdapter:
        if model:
            make_fake_model(self.models_dir, behaviour=behaviour, tamper=tamper)
        config = voice_config(models_dir=self.models_dir, worker=worker or self.worker)
        return SherpaTtsAdapter(config.voice, self.models_dir, HAANIYE, runner=interpreter_runner())

    def request(self, text: str = "سلام", timeout: float = 5.0) -> SpeechRequest:
        return SpeechRequest(text=text, output_path=self.outside / "out.wav", timeout_seconds=timeout)

    def test_default_worker_is_the_repository_script(self):
        self.assertEqual(default_worker_script().name, "sherpa_tts_worker.py")
        self.assertTrue(default_worker_script().is_file())

    def test_available_when_runtime_worker_model_and_manifest_agree(self):
        availability = self.adapter().availability()
        self.assertTrue(availability.available, availability.reasons)
        self.assertEqual(availability.detail["manifest"], "verified")

    def test_missing_model_is_reported(self):
        adapter = self.adapter(model=False)
        availability = adapter.availability()
        self.assertFalse(availability.available)
        self.assertEqual(availability.error_code, ErrorCode.VOICE_MODEL_MISSING)
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_MODEL_MISSING)

    def test_missing_runtime_interpreter_is_reported(self):
        make_fake_model(self.models_dir)
        config = voice_config(models_dir=self.models_dir, worker=self.worker, runtime_python=self.tmp_path / "venv" / "bin" / "python")
        adapter = SherpaTtsAdapter(config.voice, self.models_dir, HAANIYE)
        availability = adapter.availability()
        self.assertFalse(availability.available)
        self.assertEqual(availability.error_code, ErrorCode.VOICE_RUNTIME_MISSING)

    def test_interpreter_outside_the_runtime_root_is_rejected(self):
        make_fake_model(self.models_dir)
        config = voice_config(models_dir=self.models_dir, worker=self.worker)
        from primee.voice.process import SafeProcessRunner

        adapter = SherpaTtsAdapter(config.voice, self.models_dir, HAANIYE, runner=SafeProcessRunner([self.tmp_path]))
        availability = adapter.availability()
        self.assertFalse(availability.available)
        self.assertEqual(availability.error_code, ErrorCode.VOICE_EXECUTABLE_REJECTED)

    def test_missing_worker_script_is_reported(self):
        adapter = self.adapter(worker=self.tmp_path / "missing_worker.py")
        availability = adapter.availability()
        self.assertFalse(availability.available)
        self.assertTrue(any("worker" in reason for reason in availability.reasons))

    def test_hash_mismatch_blocks_synthesis(self):
        adapter = self.adapter(tamper=True)
        availability = adapter.availability()
        self.assertFalse(availability.available)
        self.assertEqual(availability.error_code, ErrorCode.VOICE_HASH_MISMATCH)
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_HASH_MISMATCH)

    def test_synthesis_writes_a_wav_and_reports_metrics(self):
        adapter = self.adapter()
        result = adapter.synthesize(self.request("برنامه امروز شما آماده است."))
        self.assertTrue(result.output_path.is_file())
        self.assertEqual(result.sample_rate, 16000)
        self.assertEqual(result.num_samples, 4000)
        self.assertAlmostEqual(result.audio_seconds, 0.25, places=3)
        self.assertGreater(result.output_bytes, 44)
        self.assertIsNotNone(result.real_time_factor)
        self.assertTrue(any("TBD" in warning for warning in result.warnings))
        self.assertNotIn("برنامه", json.dumps(result.describe(), ensure_ascii=False))

    def test_text_goes_over_stdin_never_into_arguments(self):
        adapter = self.adapter(behaviour="echo")
        with self.assertRaises(VoiceError):  # the echo stand-in is not a valid worker result
            adapter.synthesize(self.request("متن محرمانه"))
        # Prove the text is delivered via stdin by running the worker through the same runner.
        from primee.voice.process import ProcessSpec

        outcome = interpreter_runner().run(
            ProcessSpec(
                executable=adapter.interpreter(),
                arguments=("-I", str(self.worker), "--model", str(adapter.model_paths()["model"]), "--tokens", str(adapter.model_paths()["tokens"]), "--data-dir", str(adapter.model_paths()["data_dir"]), "--output", str(self.outside / "e.wav")),
                stdin_text="متن محرمانه",
                timeout_seconds=10,
            )
        )
        self.assertEqual(json.loads(outcome.stdout)["text"], "متن محرمانه")

    def test_worker_failure_is_a_process_error_and_leaves_no_file(self):
        adapter = self.adapter(behaviour="nonzero")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_PROCESS_FAILED)
        self.assertEqual(caught.exception.detail["returncode"], 5)
        self.assertFalse((self.outside / "out.wav").exists())

    def test_worker_timeout(self):
        adapter = self.adapter(behaviour="sleep")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request(timeout=1))
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_PROCESS_TIMEOUT)

    def test_invalid_worker_output(self):
        adapter = self.adapter(behaviour="badjson")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_OUTPUT_INVALID)

    def test_missing_wav_despite_success_report(self):
        adapter = self.adapter(behaviour="nowav")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_OUTPUT_INVALID)

    def test_wav_that_disagrees_with_the_report_is_rejected(self):
        adapter = self.adapter(behaviour="mismatch")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_OUTPUT_INVALID)
        self.assertFalse((self.outside / "out.wav").exists())

    def test_excessive_worker_output_is_bounded_and_rejected(self):
        adapter = self.adapter(behaviour="spam")
        with self.assertRaises(VoiceError) as caught:
            adapter.synthesize(self.request())
        self.assertEqual(caught.exception.code, ErrorCode.VOICE_OUTPUT_INVALID)

    def test_request_validation(self):
        adapter = self.adapter()
        with self.assertRaises(VoiceError):
            adapter.synthesize(SpeechRequest(text="", output_path=self.outside / "a.wav"))
        with self.assertRaises(VoiceError):
            adapter.synthesize(SpeechRequest(text="x" * 3000, output_path=self.outside / "a.wav"))
        (self.outside / "exists.wav").write_bytes(b"")
        with self.assertRaises(VoiceError):
            adapter.synthesize(SpeechRequest(text="x", output_path=self.outside / "exists.wav"))


if __name__ == "__main__":
    unittest.main()
