"""Primee Voice: an optional, local-only speech layer (Step Three).

Nothing in this package is required for Primee to run. Every module here is
standard-library Python; the actual speech engine (sherpa-onnx) and the speech
model (Haaniye) are third-party files installed *outside* the repository, in an
isolated Python environment, and are reached only through one hardened
subprocess boundary in :mod:`primee.voice.process`.

The text answer is always shown on screen. Speech is an extra, never a
replacement, and every failure path returns to text.
"""

from __future__ import annotations

__all__ = ["VOICE_STEP", "VOICE_STATUS"]

VOICE_STEP = "three"
#: Honest inventory. Push-to-talk, speech-to-text and the full loop are not
#: implemented yet; only the text-to-speech benchmark path exists.
VOICE_STATUS = "tts-benchmark-preparation"
