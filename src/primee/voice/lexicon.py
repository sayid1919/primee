"""A small, editable pronunciation lexicon applied to the spoken text only.

Persian writing leaves short vowels out, so a phonemiser has to guess them for
any word outside its dictionary and sometimes guesses wrong. The lexicon maps
a written word to the spelling that *sounds* right (for example with explicit
vowel marks). It touches nothing but the string handed to the speech engine:
what is shown on screen, logged or stored never changes.

The file is plain TOML with one table::

    [words]
    "پرایمی" = "پِرایمی"

Rules are whole-word, case-sensitive, applied longest-first, never chained.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from ..core.errors import ErrorCode, VoiceError

MAX_ENTRIES = 2000
MAX_WORD_LENGTH = 64
MAX_FILE_BYTES = 256 * 1024
#: Format characters that are part of a word in Persian: ZWNJ and ZWJ.
_WORD_JOINERS = frozenset("\u200c\u200d")
#: Punctuation that may appear inside a written word.
_WORD_PUNCTUATION = frozenset("-'’")


def _is_single_word(word: str) -> bool:
    """Letters, digits, combining marks (vowel signs) and joiners only."""
    for ch in word:
        if ch.isspace():
            return False
        category = unicodedata.category(ch)
        if category in ("Cc", "Cs", "Co", "Cn"):
            return False
        if category == "Cf" and ch not in _WORD_JOINERS:
            return False
        if category[0] in ("P", "S", "Z") and ch not in _WORD_PUNCTUATION:
            return False
    return True


@dataclass(frozen=True)
class Lexicon:
    entries: Mapping[str, str]
    source: str = ""

    def __len__(self) -> int:
        return len(self.entries)

    def apply(self, text: str) -> str:
        if not self.entries or not isinstance(text, str) or not text:
            return text
        normalized = unicodedata.normalize("NFC", text)
        # Longest words first so a shorter entry never rewrites part of a longer one.
        ordered = sorted(self.entries, key=len, reverse=True)
        pattern = re.compile(r"(?<![\w‌‍])(" + "|".join(re.escape(word) for word in ordered) + r")(?![\w‌‍])")
        return pattern.sub(lambda match: self.entries[match.group(1)], normalized)

    def describe(self) -> dict:
        return {"entries": len(self.entries), "source": self.source}


EMPTY = Lexicon(entries={}, source="")


def load_lexicon(path: Path) -> Lexicon:
    path = Path(path)
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            raise VoiceError(ErrorCode.CONFIG_INVALID, "The pronunciation lexicon file is too large.")
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise VoiceError(ErrorCode.CONFIG_INVALID, "The pronunciation lexicon file does not exist.") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise VoiceError(ErrorCode.CONFIG_INVALID, "The pronunciation lexicon is not valid TOML.") from exc
    return parse_lexicon(raw, source=path.name)


def parse_lexicon(raw: Mapping, *, source: str = "") -> Lexicon:
    words = raw.get("words") if isinstance(raw, Mapping) else None
    if not isinstance(words, Mapping):
        raise VoiceError(ErrorCode.CONFIG_INVALID, "The lexicon needs a [words] table.")
    if len(words) > MAX_ENTRIES:
        raise VoiceError(ErrorCode.CONFIG_INVALID, "The lexicon has too many entries.")
    entries: dict[str, str] = {}
    for key, value in words.items():
        word = unicodedata.normalize("NFC", str(key)).strip()
        spoken = unicodedata.normalize("NFC", str(value)).strip()
        if not word or not spoken or len(word) > MAX_WORD_LENGTH or len(spoken) > 4 * MAX_WORD_LENGTH:
            raise VoiceError(ErrorCode.CONFIG_INVALID, "A lexicon entry is empty or too long.")
        if not _is_single_word(word):
            raise VoiceError(ErrorCode.CONFIG_INVALID, "A lexicon key must be a single word without spaces or punctuation.")
        if any(unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn") for ch in spoken):
            raise VoiceError(ErrorCode.CONFIG_INVALID, "A lexicon value contains a control character.")
        entries[word] = spoken
    return Lexicon(entries=entries, source=source)
