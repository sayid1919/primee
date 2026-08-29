"""Primee Memory — the service behind the Vault skill.

Everything here operates on plain Markdown files under one configured root.
The Markdown is the memory: the page cache below is a convenience rebuilt from
those files on demand and is never authoritative.  If a write did not reach the
Vault, no operation reports success, and Primee does not claim to remember it.

This module is reachable only through the Vault skill, which Primee Core calls
after the permission and approval gates have already run.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ..core.clock import Clock, timestamp_iso
from ..core.errors import ErrorCode, PrimeeError
from ..core.paths import normalize_relative_path
from ..core.redaction import redact_text
from . import search as search_module  # noqa: F401
from .changelog import ChangeEvent, append_entries, count_entries, verify_append_only
from .index_builder import IndexEntry, build_index
from .initializer import InitPlan, initialize_vault
from .links import LinkGraph, extract_links, normalize_target, plan_rename, rewrite_links
from .page import Page, parse_page, parse_page_lenient, render_page
from .schema import (
    CHANGELOG_FILE,
    CONTENT_FOLDERS,
    FINAL_STATUSES,
    INDEX_FILE,
    OUTPUTS,
    OUTPUT_TYPES,
    PRIMEE_FILE,
    RAW,
    RAW_TYPES,
    ROOT_FILES,
    SCHEMA_VERSION,
    WIKI,
    WIKI_TYPES,
    PageMetadata,
    SchemaError,
    build_metadata,
    check_type_matches_path,
    content_hash,
    generate_id,
)

MAX_SLUG = 60
MAX_COLLISION_ATTEMPTS = 50
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")

#: Root files Primee manages itself. A caller can never target them with a
#: generic file operation or a page operation.
RESERVED_PATHS = frozenset(ROOT_FILES)

_SLUG_STRIP = re.compile(r"[^\w؀-ۿ‌-]+", re.UNICODE)
_SLUG_DASHES = re.compile(r"-{2,}")


def slugify(title: str, *, fallback: str = "untitled") -> str:
    """Turn a title into a safe, readable filename stem.

    Persian and other non-Latin titles keep their letters; only separators and
    punctuation are replaced.
    """
    text = unicodedata.normalize("NFC", str(title)).strip().casefold()
    text = text.replace(" ", "-").replace("_", "-")
    text = _SLUG_STRIP.sub("-", text)
    text = _SLUG_DASHES.sub("-", text).strip("-.")
    text = text[:MAX_SLUG].strip("-.")
    return text or fallback


@dataclass(frozen=True)
class MemoryResult:
    """What one memory operation did."""

    operation: str
    ok: bool
    message: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"operation": self.operation, "ok": self.ok, "message": self.message, **self.data}


class MemoryService:
    """The Primee Memory API. Constructed by Primee Core, used by the Vault skill."""

    def __init__(self, storage, clock: Clock, *, actor: str = "vault") -> None:
        self.storage = storage
        self.clock = clock
        self.actor = actor
        self._cache: Optional[list[Page]] = None
        self._broken: list[dict] = []

    # -- helpers --------------------------------------------------------
    @property
    def root(self) -> Path:
        return self.storage.root

    def now(self) -> str:
        return timestamp_iso(self.clock)

    def invalidate(self) -> None:
        """Drop the in-memory page cache. The Markdown remains the truth."""
        self._cache = None
        self._broken = []

    def _guard_reserved(self, relative_path: str) -> str:
        path = normalize_relative_path(relative_path)
        if path in RESERVED_PATHS:
            raise PrimeeError(
                ErrorCode.VAULT_PATH_REJECTED,
                f"'{path}' is managed by Primee and cannot be written directly. "
                "Use rebuild_index or append_change instead.",
            )
        return path

    def _page_paths(self) -> list[str]:
        return [
            entry.path
            for entry in self.storage.list()
            if entry.path.endswith(".md")
            and entry.path not in RESERVED_PATHS
            and entry.path.split("/")[0] in CONTENT_FOLDERS
            and not entry.path.split("/")[-1].startswith(".")
        ]

    def pages(self, *, refresh: bool = False) -> list[Page]:
        """Every valid page, parsed fresh from Markdown."""
        if self._cache is not None and not refresh:
            return self._cache
        pages: list[Page] = []
        broken: list[dict] = []
        for path in sorted(self._page_paths()):
            try:
                text = self.storage.read(path)
            except PrimeeError as exc:
                broken.append({"path": path, "error": exc.sanitized_message()})
                continue
            page, error = parse_page_lenient(text, relative_path=path)
            if page is None:
                broken.append({"path": path, "error": error})
            else:
                pages.append(page)
        self._cache = pages
        self._broken = broken
        return pages

    def broken_pages(self) -> list[dict]:
        self.pages()
        return list(self._broken)

    def page_at(self, relative_path: str) -> Page:
        path = normalize_relative_path(relative_path)
        text = self.storage.read(path)
        return parse_page(text, relative_path=path)

    def find_by_target(self, target: str) -> Optional[Page]:
        wanted = normalize_target(target)
        for page in self.pages():
            if page.link_target == wanted:
                return page
        matches = [p for p in self.pages() if p.link_target.rsplit("/", 1)[-1] == wanted]
        return matches[0] if len(matches) == 1 else None

    def link_graph(self) -> LinkGraph:
        return LinkGraph.build(
            {p.link_target: (p.metadata.title, p.body) for p in self.pages()}
        )

    def _unique_path(self, folder: str, stem: str) -> str:
        candidate = f"{folder}/{stem}.md"
        if not self.storage.exists(candidate):
            return candidate
        for counter in range(2, MAX_COLLISION_ATTEMPTS + 1):
            candidate = f"{folder}/{stem}-{counter}.md"
            if not self.storage.exists(candidate):
                return candidate
        raise PrimeeError(
            ErrorCode.VAULT_FILE_EXISTS,
            f"Could not find a free filename for '{stem}' after "
            f"{MAX_COLLISION_ATTEMPTS} attempts.",
        )

    def dated_stem(
        self, title: str, *, now: Optional[str] = None, date_hint: str = ""
    ) -> str:
        """``YYYY-MM-DD-HHMMSS-short-title`` — ISO date first, always sortable.

        ``date_hint`` lets a skill file an output under the day it is *about*
        rather than the moment it was generated. A plan produced late on Friday
        for Saturday belongs next to Saturday's other pages. The frontmatter
        keeps the literal creation time either way, so nothing is misreported.
        """
        moment = now or self.now()
        date_part = _ISO_DATE_RE.match(date_hint.strip()).group(0) if (
            date_hint and _ISO_DATE_RE.match(date_hint.strip())
        ) else moment[:10]
        time_part = moment[11:19].replace(":", "")
        slug = slugify(title)
        # A title such as "Daily plan 2026-08-27" would otherwise repeat the
        # date in the filename. The prefix already carries it.
        slug = _SLUG_DASHES.sub("-", slug.replace(date_part, "")).strip("-") or "untitled"
        return f"{date_part}-{time_part}-{slug}"

    # -- changelog ------------------------------------------------------
    def append_change(self, event: ChangeEvent) -> None:
        """Append one event. Existing history is verified, never rewritten."""
        try:
            existing = self.storage.read(CHANGELOG_FILE)
        except PrimeeError as exc:
            if exc.code != ErrorCode.VAULT_FILE_MISSING:
                raise
            existing = None
        updated = append_entries(existing, [event])
        if existing is not None:
            verify_append_only(existing, updated)
        if existing is None:
            self.storage.create(CHANGELOG_FILE, updated)
        else:
            self.storage.update(CHANGELOG_FILE, updated)

    def _record(
        self,
        operation: str,
        *,
        page_id: str = "",
        path: str = "",
        actor: str = "",
        approval_state: str = "auto",
        result: str = "ok",
        rendered: str = "",
    ) -> ChangeEvent:
        event = ChangeEvent(
            timestamp=self.now(),
            operation=operation,
            page_id=page_id,
            path=path,
            actor=actor or self.actor,
            approval_state=approval_state,
            result=redact_text(result),
            content_hash=content_hash(rendered) if rendered else "",
        )
        self.append_change(event)
        return event

    # -- structure ------------------------------------------------------
    def initialize(self, root: object, *, dry_run: bool = False, adopt_non_empty: bool = False) -> InitPlan:
        return initialize_vault(root, self.clock, dry_run=dry_run, adopt_non_empty=adopt_non_empty)

    def validate_vault(self) -> MemoryResult:
        """Structural, schema and link check over the whole Vault."""
        missing_folders = [f for f in CONTENT_FOLDERS if not (self.root / f).is_dir()]
        missing_files = [f for f in ROOT_FILES if not (self.root / f).is_file()]

        pages = self.pages(refresh=True)
        broken = self.broken_pages()

        by_id: dict[str, list[str]] = {}
        for page in pages:
            by_id.setdefault(page.metadata.id, []).append(page.relative_path)
        duplicate_ids = {
            page_id: sorted(paths) for page_id, paths in sorted(by_id.items()) if len(paths) > 1
        }

        graph = self.link_graph()
        summary = graph.summary()
        healthy = not (missing_folders or missing_files or broken or duplicate_ids)

        return MemoryResult(
            operation="validate",
            ok=healthy,
            message=(
                f"Vault is valid: {len(pages)} page(s), {summary['links']} link(s)."
                if healthy
                else "The Vault has problems that need attention."
            ),
            data={
                "root": str(self.root),
                "page_count": len(pages),
                "missing_folders": missing_folders,
                "missing_root_files": missing_files,
                "broken_pages": broken,
                "duplicate_ids": duplicate_ids,
                "dangling_links": summary["dangling"],
                "ambiguous_links": summary["ambiguous"],
                "duplicate_page_names": summary["duplicate_names"],
                "changelog_entries": self._changelog_count(),
            },
        )

    def _changelog_count(self) -> int:
        try:
            return count_entries(self.storage.read(CHANGELOG_FILE))
        except PrimeeError:
            return 0

    # -- reading --------------------------------------------------------
    def read_page(self, relative_path: str) -> MemoryResult:
        page = self.page_at(relative_path)
        return MemoryResult(
            operation="read",
            ok=True,
            message=f"Read '{page.relative_path}'.",
            data={
                "path": page.relative_path,
                "link_target": page.link_target,
                "metadata": page.metadata.to_mapping(),
                "body": page.body,
                "content": page.render(),
                "links": [link.to_dict() for link in extract_links(page.body)],
            },
        )

    def search(self, mode: str, **kwargs) -> MemoryResult:
        pages = self.pages()
        limit = int(kwargs.get("limit", 20) or 20)
        if mode == "text":
            hits = search_module.search_text(pages, str(kwargs.get("query", "")), limit=limit)
            label = f"text '{kwargs.get('query', '')}'"
        elif mode == "tag":
            hits = search_module.search_tag(pages, str(kwargs.get("tag", "")), limit=limit)
            label = f"tag '{kwargs.get('tag', '')}'"
        elif mode == "metadata":
            try:
                hits = search_module.search_metadata(
                    pages, str(kwargs.get("field", "")), str(kwargs.get("value", "")), limit=limit
                )
            except ValueError as exc:
                raise PrimeeError(ErrorCode.INVALID_INPUT, str(exc)) from exc
            label = f"{kwargs.get('field')} = '{kwargs.get('value')}'"
        elif mode == "recent":
            hits = search_module.recent(pages, limit=limit, folder=kwargs.get("folder"))
            label = "most recently updated"
        else:  # pragma: no cover - guarded by the operation catalogue
            raise PrimeeError(ErrorCode.INVALID_INPUT, f"Unknown search mode {mode!r}.")
        return MemoryResult(
            operation=f"search_{mode}",
            ok=True,
            message=f"{len(hits)} page(s) matched {label}.",
            data={"results": [hit.to_dict() for hit in hits], "count": len(hits)},
        )

    def backlinks(self, target: str) -> MemoryResult:
        graph = self.link_graph()
        wanted = normalize_target(target)
        sources = graph.backlinks(wanted)
        return MemoryResult(
            operation="backlinks",
            ok=True,
            message=f"{len(sources)} page(s) link to '{wanted}'.",
            data={
                "target": wanted,
                "exists": wanted in graph.pages,
                "backlinks": sources,
            },
        )

    def validate_links(self) -> MemoryResult:
        summary = self.link_graph().summary()
        clean = not summary["dangling"] and not summary["ambiguous"]
        return MemoryResult(
            operation="validate_links",
            ok=clean,
            message=(
                f"All {summary['links']} link(s) resolve."
                if clean
                else f"{len(summary['dangling'])} dangling and "
                f"{len(summary['ambiguous'])} ambiguous link(s) found. "
                "They are reported, never removed."
            ),
            data=summary,
        )

    # -- writing --------------------------------------------------------
    def _write_page(
        self,
        relative_path: str,
        metadata: PageMetadata,
        body: str,
        *,
        mode: str,
    ) -> tuple[str, str]:
        path = self._guard_reserved(relative_path)
        check_type_matches_path(metadata.type, path)
        rendered = render_page(metadata, body)
        if mode == "create":
            self.storage.create(path, rendered)
        else:
            self.storage.update(path, rendered)
        self.invalidate()
        return path, rendered

    def create_raw(
        self,
        *,
        title: str,
        body: str,
        summary: str,
        tags: Iterable[str] = (),
        source: str = "",
        source_type: str = "manual",
        sensitivity: str = "private",
        related: Iterable[str] = (),
        actor: str = "",
        captured_at: str = "",
    ) -> MemoryResult:
        """Capture a new raw note. Raw notes are immutable once accepted."""
        now = self.now()
        created = captured_at.strip() or now
        stem = self.dated_stem(title, now=created)
        path = self._unique_path(RAW, stem)
        metadata = build_metadata(
            {
                "id": generate_id("raw_note", relative_path=path, title=title, created=created),
                "schema_version": SCHEMA_VERSION,
                "title": title,
                "type": "raw_note",
                "tags": list(tags),
                "created": created,
                "updated": created,
                "summary": summary,
                "source": source,
                "source_type": source_type,
                "sensitivity": sensitivity,
                "status": "captured",
                "related": list(related),
            },
            relative_path=path,
        )
        path, rendered = self._write_page(path, metadata, body, mode="create")
        self._record("create_raw", page_id=metadata.id, path=path, actor=actor, rendered=rendered)
        return MemoryResult(
            operation="create_raw",
            ok=True,
            message=f"Captured raw note '{path}'.",
            data={"path": path, "link_target": path[:-3], "id": metadata.id},
        )

    def amend_raw(
        self,
        *,
        target: str,
        body: str,
        summary: str,
        title: str = "",
        tags: Iterable[str] = (),
        actor: str = "",
    ) -> MemoryResult:
        """Record a correction to a raw note without touching the original.

        The original note is never rewritten. The amendment links back to it,
        and ``backlinks`` makes the relationship discoverable from either side.
        """
        original = self.find_by_target(target)
        if original is None:
            raise PrimeeError(
                ErrorCode.VAULT_FILE_MISSING,
                f"There is no raw note at '{normalize_target(target)}' to amend.",
            )
        if original.metadata.type not in RAW_TYPES:
            raise PrimeeError(
                ErrorCode.INVALID_INPUT,
                f"'{original.link_target}' is a {original.metadata.type}, not a raw note. "
                "Amendments apply to raw captures only.",
            )

        now = self.now()
        amendment_title = title.strip() or f"Amendment to {original.metadata.title}"
        stem = self.dated_stem(amendment_title, now=now)
        path = self._unique_path(RAW, stem)
        linked_body = (
            f"> Amendment to [[{original.link_target}]]. "
            "The original note is preserved unchanged.\n\n" + body.strip()
        )
        metadata = build_metadata(
            {
                "id": generate_id("raw_amendment", relative_path=path, title=amendment_title, created=now),
                "schema_version": SCHEMA_VERSION,
                "title": amendment_title,
                "type": "raw_amendment",
                "tags": list(tags) or list(original.metadata.tags),
                "created": now,
                "updated": now,
                "summary": summary,
                "source": original.metadata.source,
                "source_type": original.metadata.source_type,
                "sensitivity": original.metadata.sensitivity,
                "status": "captured",
                "related": [original.link_target],
                "supersedes": original.link_target,
            },
            relative_path=path,
        )
        path, rendered = self._write_page(path, metadata, linked_body, mode="create")
        self._record("amend_raw", page_id=metadata.id, path=path, actor=actor, rendered=rendered)
        return MemoryResult(
            operation="amend_raw",
            ok=True,
            message=f"Recorded amendment '{path}' for '{original.link_target}'.",
            data={
                "path": path,
                "link_target": path[:-3],
                "id": metadata.id,
                "amends": original.link_target,
                "original_unchanged": True,
            },
        )

    def write_wiki(
        self,
        *,
        title: str,
        body: str,
        summary: str,
        slug: str = "",
        tags: Iterable[str] = (),
        related: Iterable[str] = (),
        status: str = "active",
        sensitivity: str = "private",
        source: str = "",
        allow_update: bool = False,
        actor: str = "",
    ) -> MemoryResult:
        """Create a wiki topic, or update it when ``allow_update`` is set.

        Updating requires the caller to have passed through the ``update_wiki``
        operation, which needs the ``vault.update`` permission.
        """
        stem = slugify(slug or title)
        path = f"{WIKI}/{stem}.md"
        exists = self.storage.exists(path)

        if exists and not allow_update:
            raise PrimeeError(
                ErrorCode.VAULT_FILE_EXISTS,
                f"The wiki page '{path}' already exists. Use the 'update_wiki' "
                "operation, which requires the 'vault.update' permission.",
            )
        if not exists and allow_update:
            raise PrimeeError(
                ErrorCode.VAULT_FILE_MISSING,
                f"There is no wiki page at '{path}' to update. Use 'write_wiki' to create it.",
            )

        now = self.now()
        if exists:
            previous = self.page_at(path)
            created = previous.metadata.created          # created never changes
            page_id = previous.metadata.id               # the id stays stable
            unchanged = previous.body.strip() == body.strip() and previous.metadata.title == title
            updated = previous.metadata.updated if unchanged else now
        else:
            created = now
            updated = now
            page_id = generate_id("wiki_topic", relative_path=path, title=title, created=created)
            unchanged = False

        metadata = build_metadata(
            {
                "id": page_id,
                "schema_version": SCHEMA_VERSION,
                "title": title,
                "type": "wiki_topic",
                "tags": list(tags),
                "created": created,
                "updated": updated,
                "summary": summary,
                "source": source,
                "source_type": "skill" if actor else "manual",
                "sensitivity": sensitivity,
                "status": status,
                "related": list(related),
            },
            relative_path=path,
        )
        path, rendered = self._write_page(
            path, metadata, body, mode="update" if exists else "create"
        )
        operation = "update_wiki" if exists else "write_wiki"
        self._record(operation, page_id=metadata.id, path=path, actor=actor, rendered=rendered)
        return MemoryResult(
            operation=operation,
            ok=True,
            message=(
                f"{'Updated' if exists else 'Created'} wiki page '{path}'."
                + ("" if not unchanged else " Content was unchanged, so 'updated' was kept.")
            ),
            data={
                "path": path,
                "link_target": path[:-3],
                "id": metadata.id,
                "created_new": not exists,
                "content_changed": not unchanged,
            },
        )

    def publish_output(
        self,
        *,
        title: str,
        body: str,
        summary: str,
        output_type: str = "output_report",
        tags: Iterable[str] = (),
        related: Iterable[str] = (),
        status: str = "final",
        sensitivity: str = "private",
        source: str = "",
        supersedes: str = "",
        date_hint: str = "",
        actor: str = "",
    ) -> MemoryResult:
        """Publish a new dated output. Existing outputs are never overwritten."""
        if output_type not in OUTPUT_TYPES:
            raise PrimeeError(
                ErrorCode.INVALID_INPUT,
                f"'{output_type}' is not an output type. Allowed: {', '.join(sorted(OUTPUT_TYPES))}.",
            )
        now = self.now()
        path = self._unique_path(
            OUTPUTS, self.dated_stem(title, now=now, date_hint=date_hint)
        )
        metadata = build_metadata(
            {
                "id": generate_id(output_type, relative_path=path, title=title, created=now),
                "schema_version": SCHEMA_VERSION,
                "title": title,
                "type": output_type,
                "tags": list(tags),
                "created": now,
                "updated": now,
                "summary": summary,
                "source": source,
                "source_type": "skill" if actor else "manual",
                "sensitivity": sensitivity,
                "status": status,
                "related": list(related),
                "supersedes": supersedes,
            },
            relative_path=path,
        )
        path, rendered = self._write_page(path, metadata, body, mode="create")
        self._record("publish_output", page_id=metadata.id, path=path, actor=actor, rendered=rendered)
        return MemoryResult(
            operation="publish_output",
            ok=True,
            message=f"Published output '{path}'.",
            data={"path": path, "link_target": path[:-3], "id": metadata.id, "status": status},
        )

    def revise_output(
        self,
        *,
        target: str,
        body: str,
        summary: str,
        title: str = "",
        tags: Iterable[str] = (),
        status: str = "final",
        actor: str = "",
    ) -> MemoryResult:
        """Publish a linked revision of an existing output.

        The original file is left byte-for-byte unchanged even when it is
        final: the revision carries ``supersedes``, and ``backlinks`` finds the
        revision from the original. Immutability is preserved literally rather
        than by convention.
        """
        original = self.find_by_target(target)
        if original is None:
            raise PrimeeError(
                ErrorCode.VAULT_FILE_MISSING,
                f"There is no output at '{normalize_target(target)}' to revise.",
            )
        if original.metadata.type not in OUTPUT_TYPES:
            raise PrimeeError(
                ErrorCode.INVALID_INPUT,
                f"'{original.link_target}' is a {original.metadata.type}, not an output.",
            )
        revision_title = title.strip() or f"{original.metadata.title} (revision)"
        linked_body = (
            f"> Revision of [[{original.link_target}]]. "
            "The earlier output is preserved unchanged.\n\n" + body.strip()
        )
        result = self.publish_output(
            title=revision_title,
            body=linked_body,
            summary=summary,
            output_type=original.metadata.type,
            tags=list(tags) or list(original.metadata.tags),
            related=[original.link_target],
            status=status,
            sensitivity=original.metadata.sensitivity,
            source=original.metadata.source,
            supersedes=original.link_target,
            actor=actor,
        )
        return MemoryResult(
            operation="revise_output",
            ok=True,
            message=f"Published revision '{result.data['path']}' of '{original.link_target}'.",
            data={**result.data, "revises": original.link_target, "original_unchanged": True},
        )

    # -- index ----------------------------------------------------------
    def rebuild_index(self, *, actor: str = "") -> MemoryResult:
        """Regenerate INDEX.md from the pages' own frontmatter."""
        pages = self.pages(refresh=True)
        entries = [
            IndexEntry(
                link_target=page.link_target,
                title=page.metadata.title,
                type=page.metadata.type,
                summary=page.metadata.summary,
                updated=page.metadata.updated,
            )
            for page in pages
        ]
        content = build_index(entries, generated_at=self.now())
        if self.storage.exists(INDEX_FILE):
            self.storage.update(INDEX_FILE, content)
        else:
            self.storage.create(INDEX_FILE, content)
        self._record(
            "rebuild_index", path=INDEX_FILE, actor=actor, rendered=content,
            result=f"{len(entries)} page(s) indexed",
        )
        return MemoryResult(
            operation="rebuild_index",
            ok=True,
            message=f"Rebuilt {INDEX_FILE} from {len(entries)} page(s).",
            data={
                "path": INDEX_FILE,
                "indexed": len(entries),
                "skipped_broken": len(self.broken_pages()),
                "content_hash": content_hash(content),
            },
        )

    # -- renaming -------------------------------------------------------
    def rename_page(self, *, target: str, new_target: str, actor: str = "") -> MemoryResult:
        """Rename a page and repoint every wikilink, or refuse and explain.

        An ambiguous or colliding rename stops and asks for clarification
        instead of guessing.
        """
        graph = self.link_graph()
        affected, refusal = plan_rename(graph, target, new_target)
        if refusal:
            raise PrimeeError(ErrorCode.AMBIGUOUS_REQUEST, refusal)

        old = normalize_target(target)
        new = normalize_target(new_target)
        old_path, new_path = f"{old}.md", f"{new}.md"
        page = self.page_at(old_path)
        if page.metadata.type in RAW_TYPES:
            raise PrimeeError(
                ErrorCode.PERMISSION_DENIED,
                "Raw notes are immutable, including their filenames. Add an "
                "amendment instead of renaming the capture.",
            )
        check_type_matches_path(page.metadata.type, new_path)

        moved = build_metadata(
            {**page.metadata.to_mapping(), "updated": self.now()}, relative_path=new_path
        )
        self.storage.create(new_path, render_page(moved, page.body))
        for source in affected:
            source_page = self.page_at(f"{source}.md")
            new_body, changed = rewrite_links(source_page.body, old, new)
            if changed:
                self.storage.update(
                    f"{source}.md",
                    render_page(source_page.metadata.touched(self.now()), new_body),
                )
        self.storage.update(old_path, render_page(page.metadata, page.body))
        self.invalidate()
        self._record("rename_page", page_id=moved.id, path=new_path, actor=actor,
                     result=f"from {old_path}; {len(affected)} link source(s) updated")
        return MemoryResult(
            operation="rename_page",
            ok=True,
            message=(
                f"Copied '{old_path}' to '{new_path}' and repointed links in "
                f"{len(affected)} page(s). The original file was kept: Primee never "
                "deletes Vault content."
            ),
            data={"from": old_path, "to": new_path, "updated_sources": affected},
        )
