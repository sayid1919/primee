"""VoiceService: the one place that decides whether anything is spoken.

Order of decisions, every time:

1. the person's permission policy for ``audio.playback`` (deny by default);
2. the ``[voice]`` configuration switch;
3. whether the engine adapter is actually available and verified;
4. shortening to a summary, synthesis into a temporary file, playback, deletion.

Whatever happens, the caller gets the text back and shows it. The audit event
records counts and durations only, never the words.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.audit import AuditLog, MemorySink
from ..core.clock import Clock, SystemClock
from ..core.config import PrimeeConfig
from ..core.errors import ErrorCode, VoiceError
from ..core.permissions import APPROVAL, AUTO
from ..core.result_types import SkillResult
from .adapters.text_only import TextOnlyFallback
from .interfaces import Availability, SpeechRequest, SpeechResult, TextToSpeech
from .playback import select_player
from .profiles import VoiceProfile, get_profile
from .summarize import shorten_for_speech, spoken_summary
from .tempaudio import TemporaryWav

PLAYBACK_PERMISSION = "audio.playback"
ACTOR = "core.voice"


@dataclass(frozen=True)
class SpeechOutcome:
    """What happened to one utterance. ``text`` is for the screen, not for logs."""

    text: str
    spoken: bool
    synthesized: bool
    engine: str
    fallback_reason: Optional[str] = None
    error_code: Optional[str] = None
    result: Optional[SpeechResult] = None
    warnings: tuple[str, ...] = ()

    def describe(self) -> dict:
        """Safe for JSON output and audit: no text."""
        return {
            "spoken": self.spoken,
            "synthesized": self.synthesized,
            "engine": self.engine,
            "fallback_reason": self.fallback_reason,
            "error_code": self.error_code,
            "characters": len(self.text),
            "result": self.result.describe() if self.result else None,
            "warnings": list(self.warnings),
        }


class VoiceService:
    def __init__(
        self,
        config: PrimeeConfig,
        *,
        audit: Optional[AuditLog] = None,
        clock: Optional[Clock] = None,
        adapter: Optional[TextToSpeech] = None,
        player=None,
        temp_dir: Optional[Path] = None,
    ) -> None:
        self.config = config
        self.settings = config.voice
        self.clock = clock or SystemClock()
        self.audit = audit or AuditLog(MemorySink(), self.clock, enabled=config.audit.enabled)
        self.profile: Optional[VoiceProfile] = get_profile(self.settings.profile)
        self._adapter = adapter
        self._player = player if player is not None else select_player(self.settings.player)
        self._temp_dir = temp_dir or (config.state_directory() / "voice" / "tmp")

    # -- wiring ---------------------------------------------------------
    def adapter(self) -> TextToSpeech:
        if self._adapter is not None:
            return self._adapter
        if not self.settings.configured:
            self._adapter = TextOnlyFallback("Voice output is disabled in configuration ([voice] enabled / tts_engine).")
        elif self.profile is None:
            self._adapter = TextOnlyFallback(f"Unknown voice profile {self.settings.profile!r}.")
        elif self.settings.tts_engine == "sherpa-onnx":
            from .adapters.sherpa import SherpaTtsAdapter

            self._adapter = SherpaTtsAdapter(self.settings, self.config.models_directory(), self.profile)
        else:  # pragma: no cover - guarded by config validation
            self._adapter = TextOnlyFallback("No adapter exists for the configured engine.")
        return self._adapter

    def permission_mode(self) -> str:
        return self.config.permissions.mode_for(PLAYBACK_PERMISSION)

    # -- status ---------------------------------------------------------
    def status(self) -> dict:
        availability: Availability = self.adapter().availability()
        return {
            "implemented": "text-to-speech benchmark path only; push-to-talk and speech-to-text are not built",
            "enabled": self.settings.enabled,
            "tts_engine": self.settings.tts_engine,
            "profile": self.profile.describe() if self.profile else None,
            "permission": {"name": PLAYBACK_PERMISSION, "mode": self.permission_mode()},
            "player": {"name": getattr(self._player, "name", "none"), "available": bool(self._player.available())},
            "adapter": availability.describe(),
            "models_dir": str(self.config.models_directory()),
            "runtime_dir": self.settings.runtime_dir,
            "speak_summary_only": self.settings.speak_summary_only,
            "max_spoken_chars": self.settings.max_spoken_chars,
            "save_transcripts": self.settings.save_transcripts,
            "network": "none: no voice code opens a socket",
        }

    # -- speaking -------------------------------------------------------
    def speak_result(self, result: SkillResult, *, request_id: Optional[str] = None, approved: bool = False) -> SpeechOutcome:
        text = spoken_summary(result, max_chars=self.settings.max_spoken_chars)
        return self.speak(text, request_id=request_id, approved=approved)

    def speak(
        self,
        text: str,
        *,
        request_id: Optional[str] = None,
        approved: bool = False,
        keep_output: Optional[Path] = None,
        play: bool = True,
    ) -> SpeechOutcome:
        request_id = request_id or self.audit.new_request_id()
        shown = text if isinstance(text, str) else ""
        if self.settings.speak_summary_only:
            shown = shorten_for_speech(shown, max_chars=self.settings.max_spoken_chars)

        gate = self._gate(approved)
        if gate is not None:
            return self._fallback(request_id, shown, gate)

        adapter = self.adapter()
        availability = adapter.availability()
        if not availability.available:
            reason = availability.reasons[0] if availability.reasons else "Voice engine unavailable."
            return self._fallback(
                request_id, shown, VoiceError(availability.error_code or ErrorCode.VOICE_RUNTIME_MISSING, reason)
            )
        if not shown.strip():
            return self._fallback(request_id, shown, VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "Nothing to speak."))

        keep = keep_output is not None
        with TemporaryWav(keep_output if keep else self._temp_dir, keep=keep) as output:
            try:
                result = adapter.synthesize(
                    SpeechRequest(text=shown, output_path=output, timeout_seconds=float(self.settings.timeout_seconds))
                )
            except VoiceError as exc:
                return self._fallback(request_id, shown, exc, engine=adapter.name)

            played = False
            playback_error: Optional[VoiceError] = None
            if play:
                if self._player.available():
                    try:
                        self._player.play(output)
                        played = True
                    except VoiceError as exc:
                        playback_error = exc
                else:
                    playback_error = VoiceError(
                        ErrorCode.VOICE_PLAYBACK_UNAVAILABLE, "No audio player is available on this platform."
                    )

        outcome = SpeechOutcome(
            text=shown,
            spoken=played,
            synthesized=True,
            engine=adapter.name,
            fallback_reason=playback_error.sanitized_message() if playback_error else None,
            error_code=playback_error.code if playback_error else None,
            result=result,
            warnings=result.warnings,
        )
        self.audit.record(
            request_id=request_id,
            actor_skill=ACTOR,
            action="speak",
            outcome="spoken" if played else ("synthesized" if not play else "playback_failed"),
            approval_state="granted" if approved else "not_requested",
            permission=PLAYBACK_PERMISSION,
            error_code=outcome.error_code,
            detail={"engine": adapter.name, "characters": len(shown), **(result.describe())},
        )
        return outcome

    # -- internals ------------------------------------------------------
    def _gate(self, approved: bool) -> Optional[VoiceError]:
        mode = self.permission_mode()
        if mode == AUTO:
            pass
        elif mode == APPROVAL:
            if not approved:
                return VoiceError(
                    ErrorCode.APPROVAL_REQUIRED,
                    "Speaking aloud needs approval for this run (audio.playback is set to 'approval').",
                )
        else:
            return VoiceError(
                ErrorCode.PERMISSION_DENIED,
                "The permission policy does not allow audio.playback; text only.",
            )
        if not self.settings.configured:
            return VoiceError(ErrorCode.VOICE_DISABLED, "Voice output is disabled in configuration; text only.")
        return None

    def _fallback(self, request_id: str, shown: str, error: VoiceError, *, engine: str = "text-only") -> SpeechOutcome:
        self.audit.record(
            request_id=request_id,
            actor_skill=ACTOR,
            action="speak",
            outcome="text_only",
            permission=PLAYBACK_PERMISSION,
            error_code=error.code,
            detail={"engine": engine, "characters": len(shown), "reason": error.sanitized_message()},
        )
        return SpeechOutcome(
            text=shown,
            spoken=False,
            synthesized=False,
            engine="text-only",
            fallback_reason=error.sanitized_message(),
            error_code=error.code,
        )
