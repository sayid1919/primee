"""Shared helpers for the voice tests. Everything here is synthetic.

No test starts sherpa-onnx, downloads anything, plays audio or touches a real
model. The "runtime" is the test interpreter itself; the "worker" is a small
stand-in script that writes a silent WAV and answers like the real worker.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from primee.core.audit import AuditLog, MemorySink
from primee.core.config import PrimeeConfig, load_mapping
from primee.voice.manifest import git_blob_sha1_of, sha256_of
from primee.voice.process import SafeProcessRunner
from primee.voice.profiles import HAANIYE

from .support import fixed_clock

FAKE_REVISION = "0123456789abcdef0123456789abcdef01234567"

#: Behaves like tools/voice/sherpa_tts_worker.py without sherpa-onnx. A file
#: named ``behaviour.txt`` next to the model selects a failure mode.
FAKE_WORKER = r'''
import argparse, json, os, sys, time, wave
p = argparse.ArgumentParser(add_help=False)
for name in ("--model", "--tokens", "--data-dir", "--output"):
    p.add_argument(name, required=True)
p.add_argument("--speaker-id", type=int, default=0)
p.add_argument("--speed", type=float, default=1.0)
p.add_argument("--max-chars", type=int, default=240)
p.add_argument("--threads", type=int, default=1)
p.add_argument("--noise-scale", type=float, default=0.667)
p.add_argument("--noise-scale-w", type=float, default=0.8)
p.add_argument("--max-sentences", type=int, default=0)
a = p.parse_args()
text = sys.stdin.buffer.read().decode("utf-8")
behaviour_file = os.path.join(os.path.dirname(a.model), "behaviour.txt")
behaviour = open(behaviour_file, encoding="utf-8").read().strip() if os.path.exists(behaviour_file) else ""
if behaviour == "nonzero":
    sys.stderr.write(json.dumps({"schema": "primee-tts-worker-error", "code": "SYNTHESIS_FAILED", "message": "RuntimeError"}) + "\n")
    sys.exit(5)
if behaviour == "sleep":
    time.sleep(30)
if behaviour == "badjson":
    sys.stdout.write("not json at all\n"); sys.exit(0)
if behaviour == "spam":
    sys.stdout.write("x" * 200000 + "\n")
    sys.stderr.write("y" * 200000 + "\n")
if behaviour == "env":
    sys.stdout.write(json.dumps({"schema": "env-dump", "env": dict(os.environ), "argv0": sys.argv[0], "flags": {"isolated": sys.flags.isolated}}) + "\n"); sys.exit(0)
if behaviour == "echo":
    sys.stdout.write(json.dumps({"schema": "echo", "text": text}) + "\n"); sys.exit(0)
if behaviour == "record":
    with open(os.path.join(os.path.dirname(a.model), "last-request.json"), "w", encoding="utf-8") as h:
        json.dump({"text": text, "speed": a.speed, "noise_scale": a.noise_scale, "noise_scale_w": a.noise_scale_w, "max_sentences": a.max_sentences}, h, ensure_ascii=False)
rate = 16000
frames = int(rate * 0.25)
if behaviour != "nowav":
    with wave.open(a.output, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
reported = frames if behaviour != "mismatch" else frames + 1
sys.stdout.write(json.dumps({
    "schema": "primee-tts-worker-result", "engine": "fake", "sample_rate": rate,
    "num_samples": reported, "audio_seconds": frames / rate, "synthesis_seconds": 0.01,
    "output_bytes": os.path.getsize(a.output) if os.path.exists(a.output) else 0,
    "peak_memory_kb": None, "peak_memory_note": "fake", "characters": len(text)}) + "\n")
'''


def real_interpreter() -> Path:
    """The test interpreter's real path (symlinks resolved, as the runner demands)."""
    return Path(sys.executable).resolve()


def interpreter_runner() -> SafeProcessRunner:
    return SafeProcessRunner([real_interpreter().parent])


def write_fake_worker(directory: Path, name: str = "fake_worker.py") -> Path:
    path = Path(directory) / name
    path.write_text(FAKE_WORKER, encoding="utf-8")
    return path


def make_fake_model(models_dir: Path, *, behaviour: str = "", tamper: bool = False) -> Path:
    """Create a Haaniye-shaped model folder with a manifest whose hashes match it."""
    model_dir = Path(models_dir) / HAANIYE.model_repository.rsplit("/", 1)[-1]
    data_dir = model_dir / HAANIYE.data_dir
    data_dir.mkdir(parents=True)
    files = {
        "fa-haaniye_low.onnx": os.urandom(4096),
        "fa-haaniye_low.onnx.json": json.dumps({"audio": {"sample_rate": 16000}}).encode("utf-8"),
        "tokens.txt": b"_ 0\n^ 1\n$ 2\n",
        "espeak-ng-data/phontab": os.urandom(512),
    }
    for relative, content in files.items():
        (model_dir / relative).write_bytes(content)
    if behaviour:
        (model_dir / "behaviour.txt").write_text(behaviour, encoding="utf-8")

    entries = []
    for relative in files:
        path = model_dir / relative
        if relative.endswith(".onnx"):
            hash_type, digest = "publisher-sha256", sha256_of(path)
        else:
            hash_type, digest = "publisher-git-blob-sha1", git_blob_sha1_of(path)
        entries.append(
            {
                "path": relative,
                "url": f"https://huggingface.co/{HAANIYE.model_repository}/resolve/{FAKE_REVISION}/{relative}",
                "size": path.stat().st_size,
                "hash_type": hash_type,
                "hash": digest,
                "hash_source": "computed by the test from synthetic bytes",
            }
        )
    manifest = {
        "schema": "primee-voice-manifest",
        "schema_version": 1,
        "profile": "haaniye",
        "generated_at": "2026-09-02T12:00:00+02:00",
        "generated_by": "tests.voice_support.make_fake_model",
        "statement": "synthetic",
        "hash_note": "synthetic",
        "components": [
            {
                "component": "runtime",
                "name": "sherpa-onnx",
                "version": "1.13.7",
                "source": "https://pypi.org/pypi/sherpa-onnx/1.13.7/json",
                "license": "Apache-2.0",
                "requires": ["sherpa-onnx-core==1.13.7"],
                "files": [
                    {
                        "path": "sherpa_onnx-1.13.7-cp311-cp311-win_amd64.whl",
                        "url": "https://files.pythonhosted.org/packages/x/y/z.whl",
                        "size": 1,
                        "hash_type": "publisher-sha256",
                        "hash": "0" * 64,
                    }
                ],
            },
            {
                "component": "model",
                "name": model_dir.name,
                "revision": FAKE_REVISION,
                "source": "https://huggingface.co/api/models/csukuangfj/vits-mimic3-fa-haaniye_low",
                "license": None,
                "license_note": "no licence metadata declared",
                "files": entries,
            },
            {
                "component": "provenance",
                "name": "mycroft-haaniye_low",
                "revision": "89abcdef0123456789abcdef0123456789abcdef",
                "source": "https://github.com/MycroftAI/mimic3-voices",
                "license": "CC0",
                "files": [],
            },
        ],
    }
    (model_dir / "primee-manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    if tamper:
        with (model_dir / "tokens.txt").open("ab") as handle:
            handle.write(b"# tampered\n")
    return model_dir


def voice_config(
    *,
    enabled: bool = True,
    engine: str = "sherpa-onnx",
    models_dir: Path | None = None,
    runtime_python: Path | None = None,
    worker: Path | None = None,
    playback_mode: str = "auto",
    state_dir: Path | None = None,
    player: str = "none",
    max_spoken_chars: int = 240,
    timeout_seconds: int = 5,
) -> PrimeeConfig:
    interpreter = runtime_python or real_interpreter()
    data = {
        "runtime": {"state_dir": str(state_dir) if state_dir else ""},
        "audit": {"enabled": True},
        "permissions": {"default": "never", "modes": {"audio.playback": playback_mode}},
        "voice": {
            "enabled": enabled,
            "tts_engine": engine,
            "profile": "haaniye",
            "models_dir": str(models_dir) if models_dir else "",
            "runtime_dir": str(interpreter.parent),
            "runtime_python": str(interpreter),
            "worker_script": str(worker) if worker else "",
            "player": player,
            "max_spoken_chars": max_spoken_chars,
            "timeout_seconds": timeout_seconds,
        },
    }
    return load_mapping(data)


def memory_audit() -> AuditLog:
    return AuditLog(MemorySink(), fixed_clock(), enabled=True)


class VoiceCase(unittest.TestCase):
    """A temporary models directory, a fake worker and an isolated state directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="primee-voice-test-")
        self.tmp_path = Path(self._tmp.name)
        self.models_dir = self.tmp_path / "models"
        self.models_dir.mkdir()
        self.state_dir = self.tmp_path / "state"
        self.state_dir.mkdir()
        self.worker = write_fake_worker(self.tmp_path)
        self.outside = self.tmp_path / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        # Windows keeps read-only bits on some temp files; make removal robust.
        for path in self.tmp_path.rglob("*"):
            try:
                path.chmod(path.stat().st_mode | stat.S_IWRITE)
            except OSError:
                pass
        self._tmp.cleanup()
