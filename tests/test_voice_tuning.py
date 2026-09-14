"""Synthesis tuning, the pronunciation lexicon and the listening comparison."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from primee.core.config import ConfigError, load_mapping
from primee.core.errors import ErrorCode, VoiceError
from primee.voice.interfaces import SpeechRequest
from primee.voice.lexicon import EMPTY, Lexicon, load_lexicon, parse_lexicon
from primee.voice.profiles import HAANIYE
from primee.voice.service import VoiceService
from primee.voice.tune import TUNE_SENTENCE, TUNE_VARIANTS, run_tuning

from . import REPO_ROOT
from .support import fixed_clock
from .voice_support import VoiceCase, make_fake_model, memory_audit, voice_config


class ConfigTuningTests(unittest.TestCase):
    def test_defaults_match_the_engine_defaults(self):
        voice = load_mapping({}).voice
        self.assertEqual((voice.speed, voice.noise_scale, voice.noise_scale_w, voice.sentence_batch), (1.0, 0.667, 0.8, 0))
        self.assertIsNone(voice.lexicon)

    def test_ranges_are_enforced(self):
        for key, bad in (("speed", 3), ("speed", 0.1), ("noise_scale", -1), ("noise_scale_w", 2), ("sentence_batch", -1), ("sentence_batch", 99), ("speed", "fast")):
            with self.assertRaises(ConfigError, msg=f"{key}={bad}"):
                load_mapping({"voice": {key: bad}})

    def test_values_are_read(self):
        voice = load_mapping({"voice": {"speed": 0.9, "noise_scale": 0.5, "noise_scale_w": 0.6, "sentence_batch": 1, "lexicon": "config/x.toml"}}).voice
        self.assertEqual((voice.speed, voice.noise_scale, voice.noise_scale_w, voice.sentence_batch), (0.9, 0.5, 0.6, 1))
        self.assertEqual(voice.lexicon, "config/x.toml")

    def test_the_example_lexicon_parses_and_the_example_voice_file_names_it(self):
        lexicon = load_lexicon(REPO_ROOT / "config" / "voice-lexicon.example.toml")
        self.assertGreaterEqual(len(lexicon), 1)
        text = (REPO_ROOT / "config" / "voice.example.toml").read_text(encoding="utf-8")
        for key in ("speed", "noise_scale", "noise_scale_w", "sentence_batch", "lexicon"):
            self.assertIn(f"\n{key} = ", text, key)


class RequestParameterTests(unittest.TestCase):
    def test_request_validates_the_tuning_fields(self):
        base = dict(text="x", output_path=Path("/tmp/never-created.wav"))
        for bad in ({"noise_scale": 2.0}, {"noise_scale_w": -0.1}, {"sentence_batch": 51}, {"sentence_batch": 1.5}):
            with self.assertRaises(VoiceError, msg=str(bad)):
                SpeechRequest(**base, **bad).validate()
        SpeechRequest(**base, noise_scale=0.5, noise_scale_w=0.6, sentence_batch=0).validate()
        self.assertEqual(SpeechRequest(**base, speed=0.9).parameters()["speed"], 0.9)


class LexiconTests(unittest.TestCase):
    def test_whole_word_replacement_only(self):
        lexicon = parse_lexicon({"words": {"پرایمی": "پِرایمی", "metrics": "مِتریکس"}})
        self.assertEqual(lexicon.apply("سلام، من پرایمی هستم."), "سلام، من پِرایمی هستم.")
        self.assertEqual(lexicon.apply("وضعیت website metrics را نمایش بده"), "وضعیت website مِتریکس را نمایش بده")
        self.assertEqual(lexicon.apply("پرایمی‌ها"), "پرایمی‌ها", msg="a ZWNJ-joined suffix is part of the word")
        self.assertEqual(lexicon.apply("metricsx"), "metricsx")
        self.assertEqual(EMPTY.apply("anything"), "anything")

    def test_longest_entry_wins_and_rules_do_not_chain(self):
        lexicon = parse_lexicon({"words": {"a": "b", "b": "c", "ab": "z"}})
        self.assertEqual(lexicon.apply("a ab b"), "b z c")

    def test_rejects_bad_entries(self):
        for words in ({"two words": "x"}, {"": "x"}, {"x": ""}, {"x": "bad\x00"}, {"a,b": "x"}):
            with self.assertRaises(VoiceError, msg=str(words)):
                parse_lexicon({"words": words})
        with self.assertRaises(VoiceError):
            parse_lexicon({"nowords": {}})
        with self.assertRaises(VoiceError):
            load_lexicon(Path("/definitely/missing.toml"))


class ServiceTuningTests(VoiceCase):
    def service(self, **voice_overrides):
        make_fake_model(self.models_dir, behaviour="record")
        config = voice_config(models_dir=self.models_dir, worker=self.worker, state_dir=self.state_dir)
        if voice_overrides:
            from primee.core.config import load_mapping as _lm

            data = {
                "runtime": {"state_dir": str(self.state_dir)},
                "permissions": {"default": "never", "modes": {"audio.playback": "auto"}},
                "voice": {
                    "enabled": True, "tts_engine": "sherpa-onnx", "models_dir": str(self.models_dir),
                    "runtime_dir": str(config.voice.runtime_dir), "runtime_python": str(config.voice.runtime_python),
                    "worker_script": str(self.worker), "player": "none", "timeout_seconds": 5, **voice_overrides,
                },
            }
            config = _lm(data)
        return VoiceService(config, audit=memory_audit(), clock=fixed_clock(), temp_dir=self.state_dir / "tmp")

    def last_request(self) -> dict:
        path = self.models_dir / "vits-mimic3-fa-haaniye_low" / "last-request.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_configured_parameters_reach_the_worker(self):
        service = self.service(speed=0.9, noise_scale=0.5, noise_scale_w=0.6, sentence_batch=1)
        outcome = service.speak("سلام", play=False)
        self.assertTrue(outcome.synthesized, outcome.fallback_reason)
        sent = self.last_request()
        self.assertEqual((sent["speed"], sent["noise_scale"], sent["noise_scale_w"], sent["max_sentences"]), (0.9, 0.5, 0.6, 1))
        self.assertEqual(outcome.result.parameters, {"speed": 0.9, "noise_scale": 0.5, "noise_scale_w": 0.6, "sentence_batch": 1})

    def test_per_request_overrides_and_unknown_keys(self):
        service = self.service()
        service.speak("سلام", play=False, parameters={"sentence_batch": 3, "speed": 1.2})
        sent = self.last_request()
        self.assertEqual((sent["speed"], sent["max_sentences"]), (1.2, 3))
        outcome = service.speak("سلام", play=False, parameters={"volume": 11})
        self.assertFalse(outcome.synthesized)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_INPUT)

    def test_lexicon_changes_only_what_the_engine_hears(self):
        lexicon_path = self.tmp_path / "lex.toml"
        lexicon_path.write_text('[words]\n"پرایمی" = "پِرایمی"\n', encoding="utf-8")
        service = self.service(lexicon=str(lexicon_path))
        outcome = service.speak("من پرایمی هستم", play=False)
        self.assertEqual(outcome.text, "من پرایمی هستم", msg="the screen text is untouched")
        self.assertEqual(self.last_request()["text"], "من پِرایمی هستم")
        self.assertEqual(service.status()["lexicon"]["entries"], 1)

    def test_a_broken_lexicon_is_reported_and_ignored(self):
        lexicon_path = self.tmp_path / "broken.toml"
        lexicon_path.write_text("not toml at all [[[", encoding="utf-8")
        service = self.service(lexicon=str(lexicon_path))
        outcome = service.speak("سلام", play=False)
        self.assertTrue(outcome.synthesized)
        self.assertEqual(self.last_request()["text"], "سلام")
        self.assertIsNotNone(service.status()["lexicon"]["error"])


class TuneCommandTests(VoiceCase):
    def test_variants_are_fixed_and_cover_the_two_hypotheses(self):
        self.assertEqual(len(TUNE_VARIANTS), 5)
        keys = [key for key, _, _ in TUNE_VARIANTS]
        self.assertEqual(keys, sorted(keys))
        batches = {params["sentence_batch"] for _, _, params in TUNE_VARIANTS}
        self.assertEqual(batches, {0, 1}, msg="sentence-by-sentence versus whole text must both be present")
        self.assertTrue(any(params["noise_scale"] < 0.667 for _, _, params in TUNE_VARIANTS))
        self.assertIn("؟", TUNE_SENTENCE)

    def test_writes_one_wav_per_variant_and_a_report(self):
        make_fake_model(self.models_dir, behaviour="record")
        config = voice_config(models_dir=self.models_dir, worker=self.worker, state_dir=self.state_dir)
        service = VoiceService(config, audit=memory_audit(), clock=fixed_clock(), temp_dir=self.state_dir / "tmp")
        report = run_tuning(service, self.outside, clock=fixed_clock())
        self.assertTrue(report.ok, report.to_dict())
        self.assertEqual(len(list(report.output_dir.glob("*.wav"))), 5)
        payload = json.loads(report.report_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema"], "primee-voice-tune")
        self.assertEqual([v["parameters"] for v in payload["variants"]], [p for _, _, p in TUNE_VARIANTS])
        self.assertIsNone(payload["variants"][0]["listening"]["preferred"])

    def test_refuses_inside_the_repository_and_without_an_engine(self):
        make_fake_model(self.models_dir)
        config = voice_config(models_dir=self.models_dir, worker=self.worker, state_dir=self.state_dir)
        service = VoiceService(config, audit=memory_audit(), clock=fixed_clock(), temp_dir=self.state_dir / "tmp")
        self.assertEqual(run_tuning(service, REPO_ROOT / "benchmarks", clock=fixed_clock()).error_code, ErrorCode.INVALID_INPUT)
        config = voice_config(models_dir=self.tmp_path / "nomodels", worker=self.worker, state_dir=self.state_dir)
        service = VoiceService(config, audit=memory_audit(), clock=fixed_clock(), temp_dir=self.state_dir / "tmp")
        self.assertEqual(run_tuning(service, self.outside, clock=fixed_clock()).error_code, ErrorCode.VOICE_MODEL_MISSING)


class ListeningRecordTests(unittest.TestCase):
    def test_the_first_listening_result_is_recorded_as_accept_temporarily(self):
        result = HAANIYE.listening_result
        self.assertEqual(result["classification"], "accept temporarily")
        self.assertIn("female", result["perceived_gender"])
        self.assertIn("choppy", result["fluency"])
        self.assertIn("listening", HAANIYE.speaker_gender)
        self.assertEqual(HAANIYE.approved_use, "private local benchmark only")


if __name__ == "__main__":
    unittest.main()
