"""The generic text-to-speech contract.

A voice is replaceable. Primee Core talks to *this* interface and to nothing
engine-specific; Haaniye on sherpa-onnx is one adapter behind it, and a
text-only fallback is another. Adding a different engine later means adding
an adapter, not touching Core.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

#: Hard cap on what may ever be handed to a speech engine in one request. The
#: configured ``max_spoken_chars`` is lower; this is the ceiling it cannot exceed.
MAX_SPEECH_CHARS = 2000


@dataclass(frozen=True)
class SpeechRequest:
    """One utterance to synthesise.

    ``text`` is the exact string the engine receives. It is never logged and
    never written to the Vault by the voice layer.
    """

    text: str
    output_path: Path
    speaker_id: int = 0
    speed: float = 1.0
    timeout_seconds: float = 60.0
    noise_scale: float = 0.667
    noise_scale_w: float = 0.8
    sentence_batch: int = 0

    def validate(self) -> None:
        from ..core.errors import ErrorCode, VoiceError

        if not isinstance(self.text, str) or not self.text.strip():
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "There is no text to speak.")
        if len(self.text) > MAX_SPEECH_CHARS:
            raise VoiceError(
                ErrorCode.VOICE_TEXT_REJECTED,
                f"Speech text is longer than {MAX_SPEECH_CHARS} characters.",
            )
        if "\x00" in self.text:
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Speech text contains a NUL byte.")
        if not isinstance(self.output_path, Path) or not self.output_path.is_absolute():
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Output path must be absolute.")
        if self.output_path.exists():
            raise VoiceError(
                ErrorCode.VOICE_TEXT_REJECTED, "Output path already exists; refusing to overwrite."
            )
        if not isinstance(self.speaker_id, int) or self.speaker_id < 0:
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Speaker id must be a non-negative integer.")
        if not (0.5 <= float(self.speed) <= 2.0):
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Speed must be between 0.5 and 2.0.")
        if not (1 <= float(self.timeout_seconds) <= 600):
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Timeout must be between 1 and 600 seconds.")
        for name in ("noise_scale", "noise_scale_w"):
            if not (0.0 <= float(getattr(self, name)) <= 1.5):
                raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, f"{name} must be between 0.0 and 1.5.")
        if not isinstance(self.sentence_batch, int) or not (0 <= self.sentence_batch <= 50):
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "sentence_batch must be between 0 and 50.")

    def parameters(self) -> dict:
        return {
            "speed": round(float(self.speed), 3),
            "noise_scale": round(float(self.noise_scale), 3),
            "noise_scale_w": round(float(self.noise_scale_w), 3),
            "sentence_batch": int(self.sentence_batch),
        }


@dataclass(frozen=True)
class SpeechResult:
    """What an adapter reports back. Never contains the spoken text."""

    engine: str
    profile: str
    output_path: Path
    sample_rate: int
    num_samples: int
    audio_seconds: float
    synthesis_seconds: float
    output_bytes: int
    peak_memory_kb: Optional[int] = None
    peak_memory_note: str = ""
    warnings: tuple[str, ...] = ()
    parameters: dict = field(default_factory=dict)

    @property
    def real_time_factor(self) -> Optional[float]:
        """Synthesis time divided by audio time; below 1.0 is faster than real time."""
        if self.audio_seconds <= 0:
            return None
        return round(self.synthesis_seconds / self.audio_seconds, 3)

    def describe(self) -> dict:
        return {
            "engine": self.engine,
            "profile": self.profile,
            "sample_rate": self.sample_rate,
            "num_samples": self.num_samples,
            "audio_seconds": round(self.audio_seconds, 3),
            "synthesis_seconds": round(self.synthesis_seconds, 3),
            "real_time_factor": self.real_time_factor,
            "output_bytes": self.output_bytes,
            "peak_memory_kb": self.peak_memory_kb,
            "peak_memory_note": self.peak_memory_note,
            "warnings": list(self.warnings),
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class Availability:
    """Whether an adapter can speak right now, and if not, exactly why."""

    available: bool
    engine: str
    reasons: tuple[str, ...] = ()
    error_code: Optional[str] = None
    detail: dict = field(default_factory=dict)

    def describe(self) -> dict:
        return {
            "available": self.available,
            "engine": self.engine,
            "reasons": list(self.reasons),
            "error_code": self.error_code,
            "detail": dict(self.detail),
        }


@runtime_checkable
class TextToSpeech(Protocol):
    """Every speech engine adapter implements exactly this."""

    name: str

    def availability(self) -> Availability: ...

    def synthesize(self, request: SpeechRequest) -> SpeechResult: ...


@runtime_checkable
class AudioPlayer(Protocol):
    """Plays a finished WAV file on this computer. Nothing else."""

    name: str

    def available(self) -> bool: ...

    def play(self, path: Path) -> None: ...
