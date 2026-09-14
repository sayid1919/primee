"""Haaniye on sherpa-onnx, reached through the isolated voice runtime.

Primee never imports sherpa-onnx. It starts the *runtime's own* Python
interpreter in isolated mode on ``tools/voice/sherpa_tts_worker.py``, hands it
the text on standard input and the file paths as arguments, and reads one JSON
line back. The worker has no network code and writes exactly one WAV file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from ...core.config import VoiceSettings
from ...core.errors import ErrorCode, VoiceError
from ...core.redaction import redact_text
from ..interfaces import Availability, SpeechRequest, SpeechResult
from ..manifest import VoiceManifest, load_manifest, require_verified, verify_component
from ..process import ProcessSpec, SafeProcessRunner
from ..profiles import VoiceProfile
from ..tempaudio import inspect_wav

ENGINE = "sherpa-onnx"
MANIFEST_FILENAME = "primee-manifest.json"
WORKER_FILENAME = "sherpa_tts_worker.py"
_REQUIRED_OUTPUT_KEYS = ("sample_rate", "num_samples", "audio_seconds", "synthesis_seconds", "output_bytes")


def default_worker_script() -> Path:
    """``tools/voice/sherpa_tts_worker.py`` in this checkout."""
    return Path(__file__).resolve().parents[4] / "tools" / "voice" / WORKER_FILENAME


class SherpaTtsAdapter:
    name = ENGINE

    def __init__(
        self,
        settings: VoiceSettings,
        models_dir: Path,
        profile: VoiceProfile,
        *,
        runner: Optional[SafeProcessRunner] = None,
        worker_script: Optional[Path] = None,
        manifest_path: Optional[Path] = None,
    ) -> None:
        self.settings = settings
        self.profile = profile
        self.models_dir = Path(models_dir)
        self.model_dir = self.models_dir / profile.model_repository.rsplit("/", 1)[-1]
        self.manifest_path = Path(manifest_path) if manifest_path else self.model_dir / MANIFEST_FILENAME
        if worker_script is not None:
            self.worker_script = Path(worker_script)
        elif settings.worker_script:
            self.worker_script = Path(settings.worker_script)
        else:
            self.worker_script = default_worker_script()
        self._runner = runner
        self._manifest: Optional[VoiceManifest] = None
        self._verified = False

    # -- locations ------------------------------------------------------
    def runtime_root(self) -> Optional[Path]:
        if self.settings.runtime_dir:
            return Path(self.settings.runtime_dir)
        if self.settings.runtime_python:
            return Path(self.settings.runtime_python).parent
        return None

    def interpreter(self) -> Optional[Path]:
        if self.settings.runtime_python:
            return Path(self.settings.runtime_python)
        root = self.runtime_root()
        if root is None:
            return None
        if sys.platform == "win32":
            return root / "Scripts" / "python.exe"
        return root / "bin" / "python"

    def runner(self) -> SafeProcessRunner:
        if self._runner is None:
            root = self.runtime_root()
            if root is None:
                raise VoiceError(ErrorCode.VOICE_RUNTIME_MISSING, "No voice runtime directory is configured.")
            self._runner = SafeProcessRunner([root])
        return self._runner

    def model_paths(self) -> dict[str, Path]:
        onnx, config, tokens = self.profile.model_files
        return {
            "model": self.model_dir / onnx,
            "config": self.model_dir / config,
            "tokens": self.model_dir / tokens,
            "data_dir": self.model_dir / self.profile.data_dir,
        }

    # -- availability ---------------------------------------------------
    def availability(self) -> Availability:
        reasons: list[str] = []
        code: Optional[str] = None
        detail: dict = {"model_dir_exists": self.model_dir.is_dir()}

        interpreter = self.interpreter()
        if interpreter is None:
            reasons.append("No voice runtime is configured (voice.runtime_dir).")
            code = code or ErrorCode.VOICE_RUNTIME_MISSING
        else:
            try:
                self.runner().validate_executable(interpreter)
                detail["runtime"] = "ok"
            except VoiceError as exc:
                reasons.append(exc.sanitized_message())
                code = code or exc.code

        if not self.worker_script.is_file() or self.worker_script.suffix != ".py":
            reasons.append("The speech worker script is missing.")
            code = code or ErrorCode.VOICE_RUNTIME_MISSING

        paths = self.model_paths()
        missing = [name for name, path in paths.items() if not path.exists()]
        if missing:
            reasons.append(f"Model files are missing: {', '.join(sorted(missing))}.")
            code = code or ErrorCode.VOICE_MODEL_MISSING
        else:
            try:
                self.verify_installation()
                detail["manifest"] = "verified"
            except VoiceError as exc:
                reasons.append(exc.sanitized_message())
                code = code or exc.code

        return Availability(
            available=not reasons,
            engine=self.name,
            reasons=tuple(reasons),
            error_code=code,
            detail=detail,
        )

    def manifest(self) -> VoiceManifest:
        if self._manifest is None:
            self._manifest = load_manifest(self.manifest_path)
        return self._manifest

    def verify_installation(self) -> None:
        """Prove the installed model matches the approved manifest. Cached per adapter."""
        if self._verified:
            return
        manifest = self.manifest()
        if manifest.profile != self.profile.key:
            raise VoiceError(
                ErrorCode.VOICE_MANIFEST_INVALID,
                "The installed manifest belongs to a different voice profile.",
            )
        model = manifest.model
        if model is None:
            raise VoiceError(ErrorCode.VOICE_MANIFEST_INVALID, "The manifest has no model component.")
        require_verified(verify_component(model, self.model_dir))
        self._verified = True

    # -- synthesis ------------------------------------------------------
    def synthesize(self, request: SpeechRequest) -> SpeechResult:
        request.validate()
        availability = self.availability()
        if not availability.available:
            raise VoiceError(
                availability.error_code or ErrorCode.VOICE_RUNTIME_MISSING,
                availability.reasons[0] if availability.reasons else "Voice runtime unavailable.",
            )
        interpreter = self.interpreter()
        assert interpreter is not None  # guaranteed by availability()
        paths = self.model_paths()

        arguments = (
            "-I",
            "-B",
            "-X",
            "utf8",
            str(self.worker_script),
            "--model",
            str(paths["model"]),
            "--tokens",
            str(paths["tokens"]),
            "--data-dir",
            str(paths["data_dir"]),
            "--output",
            str(request.output_path),
            "--speaker-id",
            str(int(request.speaker_id)),
            "--speed",
            f"{float(request.speed):.2f}",
            "--max-chars",
            str(len(request.text)),
            "--noise-scale",
            f"{float(request.noise_scale):.3f}",
            "--noise-scale-w",
            f"{float(request.noise_scale_w):.3f}",
            "--max-sentences",
            str(int(request.sentence_batch)),
        )
        outcome = self.runner().run(
            ProcessSpec(
                executable=interpreter,
                arguments=arguments,
                stdin_text=request.text,
                timeout_seconds=float(request.timeout_seconds),
                cwd=self.model_dir,
            )
        )

        if outcome.timed_out:
            _remove_quietly(request.output_path)
            raise VoiceError(
                ErrorCode.VOICE_PROCESS_TIMEOUT,
                f"Speech synthesis did not finish within {int(request.timeout_seconds)} seconds.",
                detail=outcome.describe(),
            )
        if outcome.returncode != 0:
            _remove_quietly(request.output_path)
            raise VoiceError(
                ErrorCode.VOICE_PROCESS_FAILED,
                "The speech worker reported an error.",
                detail={**outcome.describe(), "worker_error": _worker_error(outcome.stderr)},
            )

        payload = _parse_worker_output(outcome.stdout)
        if outcome.stdout_truncated:
            raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech worker produced too much output.")

        info = inspect_wav(request.output_path)
        if info.sample_rate != payload["sample_rate"] or info.frames != payload["num_samples"]:
            _remove_quietly(request.output_path)
            raise VoiceError(
                ErrorCode.VOICE_OUTPUT_INVALID,
                "The audio file does not match what the speech worker reported.",
            )

        warnings: list[str] = list(self.profile.provenance_warnings())
        if outcome.stderr.strip():
            warnings.append("The speech worker wrote diagnostics to stderr (not shown).")
        return SpeechResult(
            engine=self.name,
            profile=self.profile.key,
            output_path=request.output_path,
            sample_rate=info.sample_rate,
            num_samples=info.frames,
            audio_seconds=info.seconds,
            synthesis_seconds=float(payload["synthesis_seconds"]),
            output_bytes=info.size_bytes,
            peak_memory_kb=payload.get("peak_memory_kb"),
            peak_memory_note=str(payload.get("peak_memory_note", ""))[:200],
            warnings=tuple(warnings),
            parameters=request.parameters(),
        )


def _parse_worker_output(stdout: str) -> dict:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise VoiceError(
            ErrorCode.VOICE_OUTPUT_INVALID,
            "The speech worker must print exactly one JSON line.",
            detail={"lines": len(lines)},
        )
    try:
        payload = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech worker output is not JSON.") from exc
    if not isinstance(payload, dict) or payload.get("schema") != "primee-tts-worker-result":
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech worker output has an unexpected shape.")
    for key in _REQUIRED_OUTPUT_KEYS:
        value = payload.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, f"The speech worker output field {key!r} is invalid.")
    peak = payload.get("peak_memory_kb")
    if peak is not None and (isinstance(peak, bool) or not isinstance(peak, int) or peak < 0):
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech worker reported an invalid memory figure.")
    payload["sample_rate"] = int(payload["sample_rate"])
    payload["num_samples"] = int(payload["num_samples"])
    return payload


def _worker_error(stderr: str) -> str:
    """A short, redacted hint from the worker's stderr. Never the whole stream."""
    for line in reversed(stderr.splitlines()):
        line = line.strip()
        if line:
            return redact_text(line)[:200]
    return ""


def _remove_quietly(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
