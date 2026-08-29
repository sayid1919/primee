"""Searching the Vault.

Search reads the Markdown files.  There is no index that has to be kept in
sync, and nothing is searchable that is not visible in a text editor.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

from .page import Page

#: Frontmatter fields that may be searched. Restricting the set keeps the query
#: surface small and prevents a caller from probing arbitrary attributes.
SEARCHABLE_FIELDS = (
    "id",
    "type",
    "status",
    "sensitivity",
    "source_type",
    "source",
    "supersedes",
    "superseded_by",
)

MAX_RESULTS = 200


def fold(text: object) -> str:
    return unicodedata.normalize("NFKC", str(text)).casefold()


@dataclass(frozen=True)
class SearchHit:
    path: str
    title: str
    type: str
    summary: str
    updated: str
    matched_in: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "link_target": self.path[:-3] if self.path.endswith(".md") else self.path,
            "title": self.title,
            "type": self.type,
            "summary": self.summary,
            "updated": self.updated,
            "matched_in": list(self.matched_in),
        }


def _hit(page: Page, matched_in: Iterable[str]) -> SearchHit:
    return SearchHit(
        path=page.relative_path,
        title=page.metadata.title,
        type=page.metadata.type,
        summary=page.metadata.summary,
        updated=page.metadata.updated,
        matched_in=tuple(sorted(set(matched_in))),
    )


def _sorted(hits: list[SearchHit], limit: int) -> list[SearchHit]:
    hits.sort(key=lambda h: (h.updated, h.path), reverse=True)
    return hits[: max(0, min(int(limit), MAX_RESULTS))]


def search_text(pages: Iterable[Page], query: str, *, limit: int = 20) -> list[SearchHit]:
    """Case-insensitive substring search across title, summary, tags and body."""
    needle = fold(query).strip()
    if not needle:
        return []
    hits: list[SearchHit] = []
    for page in pages:
        matched = []
        if needle in fold(page.metadata.title):
            matched.append("title")
        if needle in fold(page.metadata.summary):
            matched.append("summary")
        if any(needle in fold(tag) for tag in page.metadata.tags):
            matched.append("tags")
        if needle in fold(page.body):
            matched.append("body")
        if matched:
            hits.append(_hit(page, matched))
    return _sorted(hits, limit)


def search_tag(pages: Iterable[Page], tag: str, *, limit: int = 50) -> list[SearchHit]:
    wanted = fold(tag).strip().replace(" ", "-")
    if not wanted:
        return []
    return _sorted(
        [_hit(p, ["tags"]) for p in pages if any(fold(t) == wanted for t in p.metadata.tags)],
        limit,
    )


def search_metadata(
    pages: Iterable[Page], field: str, value: str, *, limit: int = 50
) -> list[SearchHit]:
    """Exact, case-insensitive match on one allowlisted frontmatter field."""
    name = str(field or "").strip().lower()
    if name not in SEARCHABLE_FIELDS:
        raise ValueError(
            f"'{field}' is not a searchable field. Allowed: {', '.join(SEARCHABLE_FIELDS)}."
        )
    wanted = fold(value).strip()
    hits = [
        _hit(page, [name])
        for page in pages
        if fold(getattr(page.metadata, name, "")) == wanted
    ]
    return _sorted(hits, limit)


def recent(pages: Iterable[Page], *, limit: int = 10, folder: Optional[str] = None) -> list[SearchHit]:
    prefix = f"{folder.strip('/')}/" if folder else ""
    return _sorted(
        [_hit(p, ["updated"]) for p in pages if p.relative_path.startswith(prefix)], limit
    )
