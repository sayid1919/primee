"""Reading and writing one Vault page.

A page is ``YAML frontmatter + Markdown body``.  Parsing reuses the strict
frontmatter parser from Primee Core, so a Vault page is held to the same
untrusted-input rules as a ``SKILL.md``.

Serialisation emits a deliberately small YAML dialect — quoted scalars, block
sequences, no tags, no anchors — that the strict parser can always read back.
Round-tripping is verified in the tests.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from ..core.errors import ErrorCode, PrimeeError
from ..core.frontmatter import parse_document
from .schema import PageMetadata, SchemaError, build_metadata, content_hash

MAX_BODY_BYTES = 256 * 1024
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\t": "\\t", "\r": "\\r"}


@dataclass(frozen=True)
class Page:
    """One Vault page: validated metadata plus its Markdown body."""

    metadata: PageMetadata
    body: str
    relative_path: str = ""

    @property
    def id(self) -> str:
        return self.metadata.id

    @property
    def link_target(self) -> str:
        """The wikilink target for this page: its path without the extension."""
        path = self.relative_path
        return path[:-3] if path.endswith(".md") else path

    def render(self) -> str:
        return render_page(self.metadata, self.body)

    def hash(self) -> str:
        return content_hash(self.render())


def emit_scalar(value: Any) -> str:
    """Emit one YAML scalar in the subset the strict parser accepts."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    text = unicodedata.normalize("NFC", str(value))
    for char, escape in _ESCAPES.items():
        text = text.replace(char, escape)
    return f'"{text}"'


def emit_frontmatter(data: Mapping[str, Any]) -> str:
    """Render an ordered mapping as YAML frontmatter (without delimiters)."""
    lines: list[str] = []
    for key, value in data.items():
        if isinstance(value, (list, tuple)):
            if not value:
                lines.append(f"{key}: []")
                continue
            lines.append(f"{key}:")
            lines.extend(f"  - {emit_scalar(item)}" for item in value)
        elif isinstance(value, Mapping):
            lines.append(f"{key}:")
            for inner_key, inner_value in value.items():
                lines.append(f"  {inner_key}: {emit_scalar(inner_value)}")
        else:
            lines.append(f"{key}: {emit_scalar(value)}")
    return "\n".join(lines)


def render_page(metadata: PageMetadata, body: str) -> str:
    """Serialise a page to Markdown."""
    if not isinstance(body, str):
        raise PrimeeError(ErrorCode.VAULT_CONTENT_REJECTED, "Page body must be text.")
    if len(body.encode("utf-8")) > MAX_BODY_BYTES:
        raise PrimeeError(
            ErrorCode.VAULT_CONTENT_REJECTED,
            f"Page body exceeds the {MAX_BODY_BYTES} byte limit.",
        )
    normalized = unicodedata.normalize("NFC", body).replace("\r\n", "\n").replace("\r", "\n")
    return (
        "---\n"
        + emit_frontmatter(metadata.to_mapping())
        + "\n---\n\n"
        + normalized.strip("\n")
        + "\n"
    )


def parse_page(text: str, *, relative_path: str = "") -> Page:
    """Parse Markdown into a validated :class:`Page`.

    Malformed frontmatter, a missing required field or a type that does not
    match the folder all raise, with a message safe to show the user.
    """
    if not isinstance(text, str):
        raise SchemaError("Page content must be text.")
    try:
        raw_metadata, body = parse_document(text)
    except PrimeeError as exc:
        raise SchemaError(
            f"The page frontmatter could not be parsed: {exc.message}"
        ) from exc
    metadata = build_metadata(raw_metadata, relative_path=relative_path)
    return Page(metadata=metadata, body=body, relative_path=relative_path)


def parse_page_lenient(text: str, *, relative_path: str = "") -> tuple[Page | None, str]:
    """Parse a page, returning ``(page, error_message)`` instead of raising.

    Used by validation and index rebuilding, which must report every broken
    page rather than stopping at the first one.
    """
    try:
        return parse_page(text, relative_path=relative_path), ""
    except PrimeeError as exc:
        return None, exc.sanitized_message()
