"""Deterministic construction of INDEX.md.

The index is a convenience, never a source of truth: it is rebuilt entirely
from the frontmatter of the Markdown files themselves, so deleting it loses
nothing.  The same Vault always produces byte-identical index content.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .schema import CONTENT_FOLDERS, OUTPUTS, RAW, WIKI

HEADING = "# Vault index"
GENERATED_NOTE = (
    "This file is generated from the YAML frontmatter of the pages below. "
    "It is safe to delete: `rebuild_index` recreates it exactly. Never keep "
    "information here that exists nowhere else."
)
COLUMNS = ("Page", "Title", "Type", "Summary", "Updated")

SECTION_TITLES = {
    RAW: "Raw captures",
    WIKI: "Wiki topics",
    OUTPUTS: "Outputs",
}


@dataclass(frozen=True)
class IndexEntry:
    link_target: str
    title: str
    type: str
    summary: str
    updated: str

    @property
    def folder(self) -> str:
        parts = self.link_target.split("/")
        return parts[0] if len(parts) > 1 else ""

    def sort_key(self) -> tuple:
        # Newest first inside a folder, then by path so ties are stable.
        return (self.updated, self.link_target)

    def to_row(self) -> str:
        return (
            f"| [[{self.link_target}]] | {_cell(self.title)} | {_cell(self.type)} "
            f"| {_cell(self.summary)} | {_cell(self.updated)} |"
        )


def _cell(value: str) -> str:
    """Escape a value so it cannot break out of a Markdown table cell."""
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def build_index(entries: Iterable[IndexEntry], *, generated_at: str) -> str:
    """Render INDEX.md. Duplicate link targets are collapsed, never repeated."""
    unique: dict[str, IndexEntry] = {}
    for entry in entries:
        # A later entry for the same page replaces the earlier one, so a
        # rebuild can never produce two rows for one file.
        unique[entry.link_target] = entry

    lines = [
        HEADING,
        "",
        GENERATED_NOTE,
        "",
        f"- Pages indexed: {len(unique)}",
        f"- Generated at: {generated_at}",
        "",
    ]

    for folder in CONTENT_FOLDERS:
        section = sorted(
            (e for e in unique.values() if e.folder == folder),
            key=lambda e: e.sort_key(),
            reverse=True,
        )
        lines.append(f"## {SECTION_TITLES[folder]} ({len(section)})")
        lines.append("")
        if not section:
            lines.append("_No pages yet._")
            lines.append("")
            continue
        lines.append("| " + " | ".join(COLUMNS) + " |")
        lines.append("| " + " | ".join("---" for _ in COLUMNS) + " |")
        lines.extend(entry.to_row() for entry in section)
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
