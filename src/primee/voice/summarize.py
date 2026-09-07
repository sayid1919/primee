"""Reduce a structured result to the few words that are actually spoken.

Only a short summary is ever voiced. The full result stays on screen. Nothing
from ``structured_data`` is spoken, because it can hold long lists, paths or
numbers that are meaningless as audio and might carry data the person did not
want said aloud.
"""

from __future__ import annotations

import re
import unicodedata

from ..core.result_types import SkillResult

_MARKDOWN_RE = re.compile(r"[*_`#>\[\]()|]")
_WHITESPACE_RE = re.compile(r"\s+")
#: Persian and Latin sentence terminators.
_SENTENCE_END_RE = re.compile(r"(?<=[.!?؟。])\s+")
_MIN_CHARS = 8


def spoken_summary(result: SkillResult, *, max_chars: int = 240) -> str:
    """Return the text to speak for ``result``, or an empty string for nothing."""
    if result.success:
        text = result.summary
    else:
        text = result.sanitized_error_message or result.summary
    return shorten_for_speech(text, max_chars=max_chars)


def shorten_for_speech(text: str, *, max_chars: int = 240) -> str:
    if not isinstance(text, str):
        return ""
    if max_chars < _MIN_CHARS:
        max_chars = _MIN_CHARS
    clean = unicodedata.normalize("NFC", text)
    # Line breaks and tabs are word separators, not characters to delete.
    clean = re.sub(r"[\r\n\t\f\v]", " ", clean)
    clean = _MARKDOWN_RE.sub(" ", clean)
    clean = "".join(ch for ch in clean if unicodedata.category(ch) not in ("Cc", "Cs", "Co", "Cn"))
    clean = _WHITESPACE_RE.sub(" ", clean).strip()
    if not clean:
        return ""
    if len(clean) <= max_chars:
        return clean

    spoken: list[str] = []
    length = 0
    for sentence in _SENTENCE_END_RE.split(clean):
        sentence = sentence.strip()
        if not sentence:
            continue
        extra = len(sentence) + (1 if spoken else 0)
        if length + extra > max_chars:
            break
        spoken.append(sentence)
        length += extra
    if spoken:
        return " ".join(spoken)
    # A single over-long sentence: cut at the last word boundary that fits.
    cut = clean[:max_chars]
    space = cut.rfind(" ")
    if space >= _MIN_CHARS:
        cut = cut[:space]
    return cut.rstrip(" ,;:،") + "…"
