"""Transcript handling policy for the (future) speech-to-text path.

Speech-to-text is not implemented yet. This module fixes the rule it will have
to obey, and tests pin it now: what a person says is held in memory for the one
request, is never written anywhere by default, and may be persisted only after
an explicit, per-transcript approval **and** only if configuration allows it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ..core.errors import ErrorCode, VoiceError


@dataclass
class TranscriptBuffer:
    """In-memory only. Discarded with the request."""

    allow_persistence: bool = False
    _entries: list[str] = field(default_factory=list, repr=False)

    def add(self, text: str) -> None:
        if not isinstance(text, str):
            raise VoiceError(ErrorCode.VOICE_TEXT_REJECTED, "A transcript must be text.")
        self._entries.append(text)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def persist(
        self,
        writer: Callable[[str], None],
        *,
        approved: bool = False,
        approval_note: Optional[str] = None,
    ) -> int:
        """Hand the transcript to ``writer`` only with explicit approval.

        Returns the number of entries written. Raises otherwise, so a caller
        cannot persist by accident or by default.
        """
        if not self.allow_persistence:
            raise VoiceError(
                ErrorCode.PERMISSION_DENIED,
                "Saving transcripts is disabled in configuration (voice.save_transcripts).",
            )
        if approved is not True:
            raise VoiceError(
                ErrorCode.APPROVAL_REQUIRED,
                "Saving a transcript needs explicit approval for this transcript.",
            )
        if not approval_note or not str(approval_note).strip():
            raise VoiceError(
                ErrorCode.APPROVAL_REQUIRED,
                "An approval note is required so the saved transcript records why it was kept.",
            )
        count = 0
        for entry in self._entries:
            writer(entry)
            count += 1
        return count

    def describe(self) -> dict:
        """Metadata only, never the words."""
        return {
            "entries": len(self._entries),
            "characters": sum(len(entry) for entry in self._entries),
            "persistence_allowed": self.allow_persistence,
        }
