"""Temporary audio files that never outlive the request.

Speech is synthesised into a private temporary directory, played, and deleted
immediately, on success and on failure alike. No audio is kept unless the
benchmark explicitly asks for files to be kept in a directory the person chose.
"""

from __future__ import annotations

import os
import struct
import tempfile
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.errors import ErrorCode, VoiceError

MAX_WAV_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    sample_width: int
    frames: int
    size_bytes: int

    @property
    def seconds(self) -> float:
        return self.frames / self.sample_rate if self.sample_rate > 0 else 0.0

    def describe(self) -> dict:
        return {
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "sample_width": self.sample_width,
            "frames": self.frames,
            "seconds": round(self.seconds, 3),
            "size_bytes": self.size_bytes,
        }


def inspect_wav(path: Path) -> WavInfo:
    """Read the header of a finished WAV file and prove it is one."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech engine produced no audio file.") from exc
    if size == 0:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The speech engine produced an empty audio file.")
    if size > MAX_WAV_BYTES:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The audio file is unreasonably large.")
    try:
        with wave.open(str(path), "rb") as handle:
            info = WavInfo(
                sample_rate=handle.getframerate(),
                channels=handle.getnchannels(),
                sample_width=handle.getsampwidth(),
                frames=handle.getnframes(),
                size_bytes=size,
            )
    except (wave.Error, EOFError, struct.error, OSError) as exc:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The audio file is not a valid WAV file.") from exc
    if info.sample_rate < 8000 or info.sample_rate > 96000:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The audio file has an implausible sample rate.")
    if info.channels not in (1, 2) or info.sample_width not in (1, 2, 3, 4):
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The audio file has an unsupported layout.")
    if info.frames <= 0:
        raise VoiceError(ErrorCode.VOICE_OUTPUT_INVALID, "The audio file contains no samples.")
    return info


class TemporaryWav:
    """Context manager owning one temporary WAV path.

    The file is created by the speech engine, not here; this object owns the
    *name* and guarantees deletion when the block ends, whatever happened.
    """

    def __init__(self, directory: Optional[Path] = None, *, keep: bool = False) -> None:
        self._base = Path(directory) if directory else Path(tempfile.gettempdir())
        self._keep = keep
        self.path: Optional[Path] = None
        self._private_dir: Optional[Path] = None

    def __enter__(self) -> Path:
        self._base.mkdir(parents=True, exist_ok=True)
        private = self._base / f"primee-voice-{uuid.uuid4().hex}"
        private.mkdir(mode=0o700)
        self._private_dir = private
        self.path = private / "speech.wav"
        return self.path

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._keep:
            return
        self.cleanup()

    def cleanup(self) -> None:
        if self.path is not None:
            try:
                os.remove(self.path)
            except FileNotFoundError:
                pass
            except OSError:
                # Best effort once more: a player may still hold the handle for a
                # moment on Windows. Truncate so no audio remains even then.
                try:
                    with open(self.path, "wb"):
                        pass
                    os.remove(self.path)
                except OSError:
                    pass
        if self._private_dir is not None:
            try:
                os.rmdir(self._private_dir)
            except OSError:
                pass
        self.path = None
        self._private_dir = None
