"""Wikilinks and the Vault knowledge graph.

Links are written the way a person would write them in a text editor:

    [[wiki/topic-name]]
    [[wiki/topic-name|display text]]

Resolution is plain path matching over the Markdown files themselves.  There is
no index that must be trusted, no database, and no dependency on Obsidian: the
same links are meaningful in any editor.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

WIKILINK_RE = re.compile(r"\[\[([^\[\]|\n]{1,200}?)(?:\|([^\[\]\n]{0,200}))?\]\]")

RESOLVED = "resolved"
DANGLING = "dangling"
AMBIGUOUS = "ambiguous"


def normalize_target(raw: str) -> str:
    """Normalise a link target to a Vault-relative path without an extension."""
    target = str(raw).strip().replace("\\", "/")
    while target.startswith("./"):
        target = target[2:]
    target = target.strip("/")
    if target.lower().endswith(".md"):
        target = target[:-3]
    return " ".join(target.split())


@dataclass(frozen=True)
class WikiLink:
    target: str
    display: str
    raw: str

    def to_dict(self) -> dict:
        return {"target": self.target, "display": self.display}


def extract_links(body: str) -> list[WikiLink]:
    """Return every wikilink in a page body, in document order, de-duplicated."""
    if not isinstance(body, str):
        return []
    links: list[WikiLink] = []
    seen: set[str] = set()
    for match in WIKILINK_RE.finditer(body):
        target = normalize_target(match.group(1))
        if not target or target in seen:
            continue
        seen.add(target)
        links.append(
            WikiLink(
                target=target,
                display=(match.group(2) or "").strip(),
                raw=match.group(0),
            )
        )
    return links


@dataclass(frozen=True)
class Resolution:
    source: str
    target: str
    status: str
    resolved_to: str = ""
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "status": self.status,
            "resolved_to": self.resolved_to,
            "candidates": list(self.candidates),
        }


@dataclass
class LinkGraph:
    """Forward links, backlinks and unresolved links across the whole Vault."""

    pages: dict[str, str] = field(default_factory=dict)          # link target -> title
    forward: dict[str, list[Resolution]] = field(default_factory=dict)
    _by_name: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, pages: Mapping[str, tuple[str, str]]) -> "LinkGraph":
        """Build the graph.

        ``pages`` maps a link target (path without ``.md``) to ``(title, body)``.
        """
        graph = cls(pages={target: title for target, (title, _body) in pages.items()})
        for target in graph.pages:
            graph._by_name[target.rsplit("/", 1)[-1].casefold()].append(target)
        for name in graph._by_name:
            graph._by_name[name].sort()

        for source, (_title, body) in sorted(pages.items()):
            graph.forward[source] = [
                graph.resolve(source, link.target) for link in extract_links(body)
            ]
        return graph

    def resolve(self, source: str, target: str) -> Resolution:
        normalized = normalize_target(target)
        if normalized in self.pages:
            return Resolution(source, normalized, RESOLVED, resolved_to=normalized)
        candidates = self._by_name.get(normalized.rsplit("/", 1)[-1].casefold(), [])
        if len(candidates) == 1:
            return Resolution(source, normalized, RESOLVED, resolved_to=candidates[0])
        if len(candidates) > 1:
            return Resolution(source, normalized, AMBIGUOUS, candidates=tuple(candidates))
        return Resolution(source, normalized, DANGLING)

    # -- queries --------------------------------------------------------
    def links_from(self, source: str) -> list[Resolution]:
        return list(self.forward.get(normalize_target(source), []))

    def backlinks(self, target: str) -> list[str]:
        """Every page whose body links to ``target``, sorted and de-duplicated."""
        wanted = normalize_target(target)
        found = {
            source
            for source, resolutions in self.forward.items()
            for resolution in resolutions
            if resolution.status == RESOLVED and resolution.resolved_to == wanted
        }
        return sorted(found)

    def dangling(self) -> list[Resolution]:
        return sorted(
            (r for rs in self.forward.values() for r in rs if r.status == DANGLING),
            key=lambda r: (r.source, r.target),
        )

    def ambiguous(self) -> list[Resolution]:
        return sorted(
            (r for rs in self.forward.values() for r in rs if r.status == AMBIGUOUS),
            key=lambda r: (r.source, r.target),
        )

    def duplicate_basenames(self) -> dict[str, list[str]]:
        """Page names that appear in more than one folder, which make links ambiguous."""
        return {
            name: list(targets)
            for name, targets in sorted(self._by_name.items())
            if len(targets) > 1
        }

    def summary(self) -> dict:
        return {
            "pages": len(self.pages),
            "links": sum(len(rs) for rs in self.forward.values()),
            "resolved": sum(
                1 for rs in self.forward.values() for r in rs if r.status == RESOLVED
            ),
            "dangling": [r.to_dict() for r in self.dangling()],
            "ambiguous": [r.to_dict() for r in self.ambiguous()],
            "duplicate_names": self.duplicate_basenames(),
        }


def rewrite_links(body: str, old_target: str, new_target: str) -> tuple[str, int]:
    """Point every wikilink at ``old_target`` to ``new_target``.

    Returns the rewritten body and how many links changed.  Display text is
    preserved exactly.
    """
    old = normalize_target(old_target)
    new = normalize_target(new_target)
    changed = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal changed
        if normalize_target(match.group(1)) != old:
            return match.group(0)
        changed += 1
        display = match.group(2)
        return f"[[{new}|{display}]]" if display is not None else f"[[{new}]]"

    return WIKILINK_RE.sub(_replace, body), changed


def plan_rename(
    graph: LinkGraph, old_target: str, new_target: str
) -> tuple[list[str], Optional[str]]:
    """Decide whether a rename is safe.

    Returns ``(pages_to_update, refusal_reason)``.  A rename is refused rather
    than guessed when the source is missing, the destination is taken, or the
    new name would collide with an existing page name and make links ambiguous.
    """
    old = normalize_target(old_target)
    new = normalize_target(new_target)
    if old == new:
        return [], "The new name is identical to the current one."
    if old not in graph.pages:
        return [], f"There is no page at '{old}'."
    if new in graph.pages:
        return [], f"A page already exists at '{new}'; renaming would overwrite it."

    new_name = new.rsplit("/", 1)[-1].casefold()
    collisions = [t for t in graph._by_name.get(new_name, []) if t != old]
    if collisions:
        return [], (
            f"Renaming to '{new}' would create two pages named "
            f"'{new.rsplit('/', 1)[-1]}' ({', '.join(collisions)}), which makes "
            "short wikilinks ambiguous. Choose a different name."
        )

    affected = sorted(
        source
        for source, resolutions in graph.forward.items()
        for resolution in resolutions
        if resolution.status == RESOLVED and resolution.resolved_to == old
    )
    return affected, None
