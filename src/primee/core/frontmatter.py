"""A deliberately small, strict YAML-subset parser for SKILL.md frontmatter.

SKILL.md files are treated as **untrusted configuration**.  A full YAML parser
is far more powerful than the skill contract needs, and historically YAML
features such as tags, anchors and aliases have been an attack surface.  This
module therefore implements only the subset Primee actually requires:

* block mappings with ``key: value``
* block sequences with ``- item`` and ``- key: value``
* scalars: double quoted, single quoted, plain, integers, floats, booleans, null
* empty collection literals ``[]`` and ``{}``
* ``#`` comments

Everything else is rejected with :class:`FrontmatterError`.  In particular the
parser never constructs Python objects from tags, never resolves anchors or
aliases, never merges documents, and never evaluates anything.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .errors import FrontmatterError

MAX_BYTES = 64 * 1024
MAX_LINES = 2000
MAX_DEPTH = 8
MAX_KEY_LENGTH = 64
MAX_SCALAR_LENGTH = 4000
MAX_ITEMS = 200

_DELIMITER = "---"
_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.\-]*)[ \t]*:(?:[ \t]+(.*))?$")
_INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_FLOAT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$")
_FORBIDDEN_LEADERS = ("&", "*", "!", "|", ">", "%", "@", "`", "?", ",")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/", "0": "\0"}

#: See paths.ALLOWED_FORMAT_CHARS - ZWNJ/ZWJ are real letters in Persian text.
ALLOWED_FORMAT_CHARS = frozenset("\u200c\u200d")


@dataclass(frozen=True)
class _Line:
    indent: int
    text: str
    number: int


class _Cursor:
    def __init__(self, lines: list[_Line]) -> None:
        self._lines = lines
        self._index = 0

    def peek(self) -> _Line | None:
        if self._index < len(self._lines):
            return self._lines[self._index]
        return None

    def next(self) -> _Line:
        line = self._lines[self._index]
        self._index += 1
        return line


def parse_document(text: str) -> tuple[dict[str, Any], str]:
    """Split a SKILL.md document into ``(metadata, markdown_body)``."""
    if not isinstance(text, str):
        raise FrontmatterError("SKILL.md content must be text.")
    if len(text.encode("utf-8", errors="replace")) > MAX_BYTES:
        raise FrontmatterError(f"SKILL.md is larger than the {MAX_BYTES} byte limit.")
    if "\x00" in text:
        raise FrontmatterError("SKILL.md contains a NUL byte.")

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("﻿"):
        normalized = normalized[1:]
    lines = normalized.split("\n")

    start = 0
    while start < len(lines) and lines[start].strip() == "":
        start += 1
    if start >= len(lines) or lines[start].strip() != _DELIMITER:
        raise FrontmatterError("SKILL.md must begin with a '---' frontmatter delimiter.")

    end = None
    for index in range(start + 1, len(lines)):
        stripped = lines[index].strip()
        if stripped == _DELIMITER or stripped == "...":
            end = index
            break
    if end is None:
        raise FrontmatterError("SKILL.md frontmatter is not closed by a '---' line.")

    metadata = parse_mapping_block(lines[start + 1 : end])
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return metadata, body


def parse_mapping_block(raw_lines: list[str]) -> dict[str, Any]:
    """Parse a list of raw frontmatter lines into a mapping."""
    scanned = _scan(raw_lines)
    if not scanned:
        return {}
    cursor = _Cursor(scanned)
    base_indent = scanned[0].indent
    value = _parse_mapping(cursor, base_indent, depth=1)
    remaining = cursor.peek()
    if remaining is not None:
        raise FrontmatterError(
            f"Unexpected indentation at frontmatter line {remaining.number}."
        )
    return value


def _scan(raw_lines: list[str]) -> list[_Line]:
    if len(raw_lines) > MAX_LINES:
        raise FrontmatterError("SKILL.md frontmatter has too many lines.")
    scanned: list[_Line] = []
    for offset, raw in enumerate(raw_lines, start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip(" \t"))]:
            raise FrontmatterError(
                f"Tab characters are not allowed for indentation (line {offset})."
            )
        stripped = raw.strip()
        if stripped == "" or stripped.startswith("#"):
            continue
        if stripped in ("---", "..."):
            raise FrontmatterError(f"Unexpected document delimiter at line {offset}.")
        indent = len(raw) - len(raw.lstrip(" "))
        scanned.append(_Line(indent=indent, text=raw.strip(), number=offset))
    return scanned


def _parse_mapping(cursor: _Cursor, indent: int, depth: int) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise FrontmatterError("SKILL.md frontmatter is nested too deeply.")
    mapping: dict[str, Any] = {}
    while True:
        line = cursor.peek()
        if line is None or line.indent < indent:
            break
        if line.indent > indent:
            raise FrontmatterError(
                f"Unexpected indentation at frontmatter line {line.number}."
            )
        if line.text.startswith("- ") or line.text == "-":
            break
        match = _KEY_RE.match(line.text)
        if match is None:
            raise FrontmatterError(
                f"Line {line.number} is not a valid 'key: value' entry."
            )
        key = match.group(1)
        if len(key) > MAX_KEY_LENGTH:
            raise FrontmatterError(f"Key on line {line.number} is too long.")
        if key in mapping:
            raise FrontmatterError(f"Duplicate key '{key}' on line {line.number}.")
        rest = match.group(2)
        cursor.next()
        if rest is None or rest.strip() == "":
            mapping[key] = _parse_child(cursor, indent, depth, line.number)
        else:
            mapping[key] = _parse_scalar(rest, line.number)
        if len(mapping) > MAX_ITEMS:
            raise FrontmatterError("SKILL.md frontmatter has too many keys.")
    return mapping


def _parse_child(cursor: _Cursor, parent_indent: int, depth: int, parent_line: int) -> Any:
    line = cursor.peek()
    if line is None:
        return None
    if (line.text.startswith("- ") or line.text == "-") and line.indent >= parent_indent:
        return _parse_sequence(cursor, line.indent, depth + 1)
    if line.indent > parent_indent:
        return _parse_mapping(cursor, line.indent, depth + 1)
    return None


def _parse_sequence(cursor: _Cursor, indent: int, depth: int) -> list[Any]:
    if depth > MAX_DEPTH:
        raise FrontmatterError("SKILL.md frontmatter is nested too deeply.")
    items: list[Any] = []
    while True:
        line = cursor.peek()
        if line is None or line.indent != indent:
            break
        if not (line.text.startswith("- ") or line.text == "-"):
            break
        cursor.next()
        content = line.text[2:].strip() if len(line.text) > 1 else ""
        if content == "":
            items.append(_parse_child(cursor, indent, depth, line.number))
        else:
            match = _KEY_RE.match(content)
            if match is not None:
                items.append(_parse_inline_mapping(cursor, line, match, indent, depth))
            else:
                items.append(_parse_scalar(content, line.number))
        if len(items) > MAX_ITEMS:
            raise FrontmatterError("SKILL.md frontmatter list is too long.")
    return items


def _parse_inline_mapping(
    cursor: _Cursor, line: _Line, match: re.Match[str], indent: int, depth: int
) -> dict[str, Any]:
    if depth > MAX_DEPTH:
        raise FrontmatterError("SKILL.md frontmatter is nested too deeply.")
    inner_indent = indent + 2
    key = match.group(1)
    rest = match.group(2)
    mapping: dict[str, Any] = {}
    if rest is None or rest.strip() == "":
        mapping[key] = _parse_child(cursor, indent + 1, depth, line.number)
    else:
        mapping[key] = _parse_scalar(rest, line.number)
    nxt = cursor.peek()
    if nxt is not None and nxt.indent >= inner_indent and not nxt.text.startswith("- "):
        extra = _parse_mapping(cursor, nxt.indent, depth + 1)
        for extra_key, extra_value in extra.items():
            if extra_key in mapping:
                raise FrontmatterError(
                    f"Duplicate key '{extra_key}' in list item starting on line {line.number}."
                )
            mapping[extra_key] = extra_value
    return mapping


def _strip_comment(text: str) -> str:
    out = []
    for index, char in enumerate(text):
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            break
        out.append(char)
    return "".join(out).strip()


def _parse_scalar(raw: str, line_number: int) -> Any:
    text = raw.strip()
    if text == "":
        return None
    if len(text) > MAX_SCALAR_LENGTH:
        raise FrontmatterError(f"Value on line {line_number} is too long.")

    if text[0] == '"':
        value, tail = _parse_double_quoted(text, line_number)
        _require_only_comment(tail, line_number)
        return _clean_string(value, line_number)
    if text[0] == "'":
        value, tail = _parse_single_quoted(text, line_number)
        _require_only_comment(tail, line_number)
        return _clean_string(value, line_number)

    text = _strip_comment(text)
    if text == "":
        return None
    if text == "[]":
        return []
    if text == "{}":
        return {}
    if text[0] in _FORBIDDEN_LEADERS or text[0] in "[{":
        raise FrontmatterError(
            f"Unsupported YAML syntax on line {line_number}; "
            "tags, anchors, aliases and flow collections are not allowed."
        )
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if _INT_RE.match(text):
        return int(text)
    if _FLOAT_RE.match(text):
        return float(text)
    return _clean_string(text, line_number)


def _parse_double_quoted(text: str, line_number: int) -> tuple[str, str]:
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "\\":
            index += 1
            if index >= len(text):
                raise FrontmatterError(f"Dangling escape on line {line_number}.")
            escape = text[index]
            if escape not in _ESCAPES:
                raise FrontmatterError(
                    f"Unsupported escape '\\{escape}' on line {line_number}."
                )
            out.append(_ESCAPES[escape])
            index += 1
            continue
        if char == '"':
            return "".join(out), text[index + 1 :]
        out.append(char)
        index += 1
    raise FrontmatterError(f"Unterminated double quoted string on line {line_number}.")


def _parse_single_quoted(text: str, line_number: int) -> tuple[str, str]:
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if char == "'":
            if index + 1 < len(text) and text[index + 1] == "'":
                out.append("'")
                index += 2
                continue
            return "".join(out), text[index + 1 :]
        out.append(char)
        index += 1
    raise FrontmatterError(f"Unterminated single quoted string on line {line_number}.")


def _require_only_comment(tail: str, line_number: int) -> None:
    remainder = tail.strip()
    if remainder and not remainder.startswith("#"):
        raise FrontmatterError(f"Unexpected text after quoted value on line {line_number}.")


def _clean_string(value: str, line_number: int) -> str:
    normalized = unicodedata.normalize("NFC", value)
    for char in normalized:
        category = unicodedata.category(char)
        if category == "Cc" and char not in "\n\t":
            raise FrontmatterError(f"Control character in value on line {line_number}.")
        if category == "Cf" and char not in ALLOWED_FORMAT_CHARS:
            raise FrontmatterError(
                f"Invisible formatting character in value on line {line_number}."
            )
    return normalized
