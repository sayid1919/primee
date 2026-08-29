"""The append-only Vault change log.

Every accepted change to the Vault adds one line to ``CHANGELOG.md``.  Existing
lines are never rewritten or deleted: an append verifies that the file it is
about to extend still starts with everything that was already there, and
refuses otherwise.

The log records *what happened*, never the content of a page.  Every free-text
field passes through Primee's redaction before it is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from ..core.errors import ErrorCode, PrimeeError
from ..core.redaction import redact_text

HEADING = "# Vault changelog"
NOTE = (
    "Append-only. Every accepted change to this Vault is recorded here, oldest "
    "first. Entries are never rewritten or deleted. No page content, message "
    "body or credential is ever recorded."
)
COLUMNS = ("Timestamp", "Operation", "Page ID", "Path", "Actor", "Approval", "Result", "Hash")

_HEADER_LINES = (
    HEADING,
    "",
    NOTE,
    "",
    "| " + " | ".join(COLUMNS) + " |",
    "| " + " | ".join("---" for _ in COLUMNS) + " |",
)
HEADER = "\n".join(_HEADER_LINES) + "\n"


@dataclass(frozen=True)
class ChangeEvent:
    timestamp: str
    operation: str
    page_id: str
    path: str
    actor: str
    approval_state: str
    result: str
    content_hash: str = ""

    def to_row(self) -> str:
        cells = (
            self.timestamp,
            self.operation,
            self.page_id or "-",
            self.path or "-",
            redact_text(self.actor) or "-",
            self.approval_state or "-",
            redact_text(self.result) or "-",
            self.content_hash or "-",
        )
        return "| " + " | ".join(_cell(cell) for cell in cells) + " |"

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "page_id": self.page_id,
            "path": self.path,
            "actor": redact_text(self.actor),
            "approval_state": self.approval_state,
            "result": redact_text(self.result),
            "content_hash": self.content_hash,
        }


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip() or "-"


def initial_content() -> str:
    return HEADER


def append_entries(existing: Optional[str], events: Sequence[ChangeEvent]) -> str:
    """Return the new changelog content with ``events`` appended.

    Refuses if the existing file has lost the canonical header, which is the
    cheap, honest signal that history was tampered with or the file was
    replaced by something else.
    """
    if existing is None or not existing.strip():
        base = HEADER
    else:
        normalized = existing.replace("\r\n", "\n")
        if not normalized.startswith(HEADER):
            raise PrimeeError(
                ErrorCode.VAULT_CONTENT_REJECTED,
                "CHANGELOG.md does not start with the expected Primee header, so "
                "Primee will not append to it. Repairing the changelog is a "
                "separate, explicitly approved operation that must preserve history.",
            )
        base = normalized if normalized.endswith("\n") else normalized + "\n"

    rows = "".join(event.to_row() + "\n" for event in events)
    return base + rows


def verify_append_only(previous: str, updated: str) -> None:
    """Assert that ``updated`` only adds to ``previous``."""
    old = (previous or "").replace("\r\n", "\n")
    new = (updated or "").replace("\r\n", "\n")
    if not new.startswith(old):
        raise PrimeeError(
            ErrorCode.VAULT_CONTENT_REJECTED,
            "A changelog write would have modified or removed existing history "
            "and was refused.",
        )


def count_entries(content: Optional[str]) -> int:
    if not content:
        return 0
    body = content.replace("\r\n", "\n")
    if not body.startswith(HEADER):
        return 0
    return sum(1 for line in body[len(HEADER):].splitlines() if line.strip().startswith("|"))
