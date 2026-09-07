"""Speaker output. ``winsound`` from the standard library on Windows, nothing else.

There is no network player, no browser, no third-party audio library. On a
non-Windows platform playback is simply unavailable and the text stays on
screen.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..core.errors import ErrorCode, VoiceError
from .tempaudio import inspect_wav


class NullPlayer:
    name = "none"

    def available(self) -> bool:
        return False

    def play(self, path: Path) -> None:
        raise VoiceError(ErrorCode.VOICE_PLAYBACK_UNAVAILABLE, "No audio player is configured.")


class WinsoundPlayer:
    """Blocking playback through the Windows multimedia API, standard library only."""

    name = "winsound"

    def available(self) -> bool:
        return sys.platform == "win32"

    def play(self, path: Path) -> None:
        if not self.available():
            raise VoiceError(
                ErrorCode.VOICE_PLAYBACK_UNAVAILABLE, "winsound playback exists only on Windows."
            )
        inspect_wav(path)  # never hand an unverified file to the OS
        import winsound  # noqa: PLC0415 - Windows-only standard library module

        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_NODEFAULT)
        except RuntimeError as exc:
            raise VoiceError(ErrorCode.VOICE_PLAYBACK_UNAVAILABLE, "Windows could not play the audio.") from exc


def select_player(name: str):
    if name == "winsound":
        return WinsoundPlayer()
    return NullPlayer()
