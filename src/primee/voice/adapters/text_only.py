"""The adapter that is always there: nothing is spoken, the text is shown."""

from __future__ import annotations

from ...core.errors import ErrorCode, VoiceError
from ..interfaces import Availability, SpeechRequest, SpeechResult


class TextOnlyFallback:
    """Used whenever speech is disabled, unavailable, denied or has failed."""

    name = "text-only"

    def __init__(self, reason: str = "Voice output is not enabled.") -> None:
        self.reason = reason

    def availability(self) -> Availability:
        return Availability(available=False, engine=self.name, reasons=(self.reason,), error_code=ErrorCode.VOICE_DISABLED)

    def synthesize(self, request: SpeechRequest) -> SpeechResult:
        raise VoiceError(ErrorCode.VOICE_DISABLED, self.reason)
