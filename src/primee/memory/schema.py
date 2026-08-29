"""The Primee Memory page schema.

Every page in the Vault is a plain Markdown file with YAML frontmatter.  The
Markdown *is* the memory: this module defines what valid frontmatter looks like
and rejects anything else, so a page can never carry invented or malformed
metadata.

Nothing here is a database.  Everything validated by this module can be read,
edited and understood in an ordinary text editor.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping, Optional

from ..core.errors import ErrorCode, PrimeeError

SCHEMA_VERSION = 1

# --- folders ---------------------------------------------------------------
RAW = "raw"
WIKI = "wiki"
OUTPUTS = "outputs"
CONTENT_FOLDERS = (RAW, WIKI, OUTPUTS)

INDEX_FILE = "INDEX.md"
CHANGELOG_FILE = "CHANGELOG.md"
PRIMEE_FILE = "PRIMEE.md"
ROOT_FILES = (INDEX_FILE, CHANGELOG_FILE, PRIMEE_FILE)

#: Primee never creates this file. Primee's own rules live in PRIMEE.md, and
#: the Vault must not carry configuration belonging to a development tool.
FORBIDDEN_ROOT_FILES = frozenset({"CLAUDE.md", ".claude", "claude.md"})


# --- page types ------------------------------------------------------------
@dataclass(frozen=True)
class PageTypeSpec:
    name: str
    folder: str
    id_prefix: str
    description: str
    immutable: bool = False


_TYPES: tuple[PageTypeSpec, ...] = (
    PageTypeSpec("raw_note", RAW, "raw", "Original captured material.", immutable=True),
    PageTypeSpec(
        "raw_amendment", RAW, "amd", "A correction attached to an existing raw note.", immutable=True
    ),
    PageTypeSpec("wiki_topic", WIKI, "wik", "Distilled, maintained knowledge about one topic."),
    PageTypeSpec("output_report", OUTPUTS, "out", "A report Primee produced."),
    PageTypeSpec("output_plan", OUTPUTS, "out", "A daily plan Primee produced."),
    PageTypeSpec("output_brief", OUTPUTS, "out", "A brief Primee produced."),
    PageTypeSpec("output_snapshot", OUTPUTS, "out", "A recorded observation snapshot."),
    PageTypeSpec("output_draft", OUTPUTS, "out", "A draft Primee produced."),
    PageTypeSpec("vault_index", "", "idx", "The generated Vault index."),
    PageTypeSpec("vault_changelog", "", "log", "The append-only Vault change log."),
    PageTypeSpec("vault_readme", "", "doc", "The human-readable Vault rules."),
)

PAGE_TYPES: dict[str, PageTypeSpec] = {spec.name: spec for spec in _TYPES}
RAW_TYPES = frozenset(name for name, s in PAGE_TYPES.items() if s.folder == RAW)
WIKI_TYPES = frozenset(name for name, s in PAGE_TYPES.items() if s.folder == WIKI)
OUTPUT_TYPES = frozenset(name for name, s in PAGE_TYPES.items() if s.folder == OUTPUTS)
ROOT_TYPES = frozenset(name for name, s in PAGE_TYPES.items() if s.folder == "")

#: Raw material is a historical record: once accepted it is never rewritten.
IMMUTABLE_TYPES = frozenset(name for name, s in PAGE_TYPES.items() if s.immutable)


# --- controlled vocabularies ----------------------------------------------
SENSITIVITY_VALUES = ("public", "internal", "private", "sensitive")
DEFAULT_SENSITIVITY = "private"

STATUS_VALUES = (
    "captured",    # raw: accepted as captured
    "amended",     # raw: an amendment exists for this note
    "draft",       # wiki/outputs: work in progress
    "active",      # wiki: the current maintained page
    "review",      # outputs: awaiting the user's review
    "final",       # outputs: accepted, immutable
    "shipped",     # outputs: delivered, immutable
    "superseded",  # replaced by a newer page
)

#: An output in one of these states is never overwritten. Correcting it means
#: creating a linked revision.
FINAL_STATUSES = frozenset({"final", "shipped"})

SOURCE_TYPE_VALUES = (
    "manual",
    "transcript",
    "clip",
    "import",
    "observation",
    "connector",
    "skill",
    "unknown",
)
DEFAULT_SOURCE_TYPE = "unknown"


# --- field rules -----------------------------------------------------------
REQUIRED_FIELDS = (
    "id",
    "schema_version",
    "title",
    "type",
    "tags",
    "created",
    "updated",
    "summary",
)
OPTIONAL_FIELDS = (
    "source",
    "source_type",
    "sensitivity",
    "status",
    "related",
    "supersedes",
    "superseded_by",
)
ALL_FIELDS = REQUIRED_FIELDS + OPTIONAL_FIELDS

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{3,63}$")
TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_/-]{0,39}$")
LINK_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/‌‍-]{0,199}$")
#: A link target that starts like an absolute or home-relative path.
_ABSOLUTE_LINK_RE = re.compile(r"^(?:[A-Za-z]:|/|~)")

MAX_TITLE = 200
MAX_SUMMARY = 200
MAX_TAGS = 20
MAX_RELATED = 40
MAX_SOURCE = 300


class SchemaError(PrimeeError):
    """Raised when frontmatter does not satisfy the Primee Memory schema."""

    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.MANIFEST_INVALID, message, detail=detail)


@dataclass(frozen=True)
class PageMetadata:
    """Validated frontmatter for one Vault page."""

    id: str
    title: str
    type: str
    tags: tuple[str, ...]
    created: str
    updated: str
    summary: str
    schema_version: int = SCHEMA_VERSION
    source: str = ""
    source_type: str = DEFAULT_SOURCE_TYPE
    sensitivity: str = DEFAULT_SENSITIVITY
    status: str = ""
    related: tuple[str, ...] = ()
    supersedes: str = ""
    superseded_by: str = ""

    @property
    def spec(self) -> PageTypeSpec:
        return PAGE_TYPES[self.type]

    @property
    def is_immutable_type(self) -> bool:
        return self.type in IMMUTABLE_TYPES

    @property
    def is_final_output(self) -> bool:
        return self.type in OUTPUT_TYPES and self.status in FINAL_STATUSES

    def to_mapping(self) -> dict[str, Any]:
        """Ordered mapping for serialisation. Empty optional fields are omitted."""
        data: dict[str, Any] = {
            "id": self.id,
            "schema_version": self.schema_version,
            "title": self.title,
            "type": self.type,
            "tags": list(self.tags),
            "created": self.created,
            "updated": self.updated,
            "summary": self.summary,
        }
        if self.source:
            data["source"] = self.source
        data["source_type"] = self.source_type
        data["sensitivity"] = self.sensitivity
        if self.status:
            data["status"] = self.status
        if self.related:
            data["related"] = list(self.related)
        if self.supersedes:
            data["supersedes"] = self.supersedes
        if self.superseded_by:
            data["superseded_by"] = self.superseded_by
        return data

    def touched(self, updated: str) -> "PageMetadata":
        """Return a copy with a new ``updated`` timestamp. ``created`` is fixed."""
        return replace(self, updated=validate_timestamp(updated, "updated"))


# --- validators ------------------------------------------------------------
def validate_timestamp(value: Any, field_name: str) -> str:
    """Require a timezone-aware ISO-8601 timestamp."""
    if not isinstance(value, str) or not value.strip():
        raise SchemaError(f"Field '{field_name}' must be an ISO-8601 timestamp string.")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SchemaError(
            f"Field '{field_name}' is not a valid ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SchemaError(
            f"Field '{field_name}' must include a timezone offset, for example "
            "'2026-08-29T08:00:00+03:30'."
        )
    return text


def normalize_tags(value: Any) -> tuple[str, ...]:
    """Normalise a YAML list of tags: lowercase, de-duplicated, sorted."""
    if value is None:
        return ()
    if isinstance(value, str):
        raise SchemaError("Field 'tags' must be a YAML list, not a single string.")
    if not isinstance(value, (list, tuple)):
        raise SchemaError("Field 'tags' must be a YAML list.")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise SchemaError("Field 'tags' must contain only strings.")
        tag = unicodedata.normalize("NFC", item).strip().lower().replace(" ", "-")
        if not tag:
            continue
        if not TAG_RE.match(tag):
            raise SchemaError(
                f"Tag {item!r} is invalid; use lowercase letters, digits, '-', '_' or '/'."
            )
        if tag not in seen:
            seen.append(tag)
    if len(seen) > MAX_TAGS:
        raise SchemaError(f"A page may carry at most {MAX_TAGS} tags.")
    return tuple(sorted(seen))


def _one_line(value: Any, field_name: str, limit: int) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"Field '{field_name}' must be a string.")
    text = " ".join(unicodedata.normalize("NFC", value).split())
    if not text:
        raise SchemaError(f"Field '{field_name}' must not be empty.")
    if len(text) > limit:
        raise SchemaError(f"Field '{field_name}' must be at most {limit} characters.")
    return text


def _link_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise SchemaError(f"Field '{field_name}' must be a YAML list of link targets.")
    targets: list[str] = []
    for item in value:
        target = _link_target(item, field_name)
        if target and target not in targets:
            targets.append(target)
    if len(targets) > MAX_RELATED:
        raise SchemaError(f"Field '{field_name}' has too many entries.")
    return tuple(targets)


def _link_target(value: Any, field_name: str) -> str:
    """Validate one wikilink target.

    An unsafe target is rejected, never quietly rewritten into something that
    looks safe: a link pointing outside the Vault is a mistake the user needs
    to see, not one for Primee to paper over.
    """
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SchemaError(f"Field '{field_name}' must contain link targets as strings.")
    target = unicodedata.normalize("NFC", value).strip()
    if not target:
        return ""
    # Check the shape BEFORE stripping anything, so an absolute path or a
    # traversal cannot be normalised into an acceptable-looking target.
    if _ABSOLUTE_LINK_RE.match(target) or "\\" in target:
        raise SchemaError(
            f"Field '{field_name}' contains an absolute or non-relative link "
            f"target ({value!r}). Links must be relative to the Vault root."
        )
    if ".." in target.split("/"):
        raise SchemaError(
            f"Field '{field_name}' contains a '..' traversal in a link target."
        )
    target = target.strip("/")
    if target.endswith(".md"):
        target = target[:-3]
    if not target:
        return ""
    if not LINK_TARGET_RE.match(target):
        raise SchemaError(f"Field '{field_name}' contains an invalid link target.")
    return target


def _enum(value: Any, field_name: str, allowed: tuple[str, ...], default: str = "") -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    if not isinstance(value, str):
        raise SchemaError(f"Field '{field_name}' must be a string.")
    text = value.strip().lower()
    if text not in allowed:
        raise SchemaError(
            f"Field '{field_name}' must be one of: {', '.join(allowed)}. Got {value!r}."
        )
    return text


def build_metadata(raw: Mapping[str, Any], *, relative_path: str = "") -> PageMetadata:
    """Validate raw frontmatter into a :class:`PageMetadata`.

    Missing required metadata is an error.  Primee never invents it.
    """
    if not isinstance(raw, Mapping):
        raise SchemaError("Page frontmatter must be a mapping.")

    missing = [name for name in REQUIRED_FIELDS if name not in raw]
    if missing:
        raise SchemaError(
            "Page frontmatter is missing required field(s): " + ", ".join(sorted(missing))
        )

    page_id = raw["id"]
    if not isinstance(page_id, str) or not ID_RE.match(page_id.strip()):
        raise SchemaError(
            "Field 'id' must be 4-64 characters of lowercase letters, digits, '-' or '_'."
        )
    page_id = page_id.strip()

    schema_version = raw["schema_version"]
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise SchemaError("Field 'schema_version' must be an integer.")
    if schema_version != SCHEMA_VERSION:
        raise SchemaError(
            f"Unsupported schema_version {schema_version}; this Primee understands "
            f"version {SCHEMA_VERSION}."
        )

    page_type = raw["type"]
    if not isinstance(page_type, str) or page_type.strip() not in PAGE_TYPES:
        raise SchemaError(
            f"Field 'type' must be one of: {', '.join(sorted(PAGE_TYPES))}."
        )
    page_type = page_type.strip()

    metadata = PageMetadata(
        id=page_id,
        schema_version=schema_version,
        title=_one_line(raw["title"], "title", MAX_TITLE),
        type=page_type,
        tags=normalize_tags(raw["tags"]),
        created=validate_timestamp(raw["created"], "created"),
        updated=validate_timestamp(raw["updated"], "updated"),
        summary=_one_line(raw["summary"], "summary", MAX_SUMMARY),
        source=(_one_line(raw["source"], "source", MAX_SOURCE) if raw.get("source") else ""),
        source_type=_enum(raw.get("source_type"), "source_type", SOURCE_TYPE_VALUES, DEFAULT_SOURCE_TYPE),
        sensitivity=_enum(raw.get("sensitivity"), "sensitivity", SENSITIVITY_VALUES, DEFAULT_SENSITIVITY),
        status=_enum(raw.get("status"), "status", STATUS_VALUES, ""),
        related=_link_list(raw.get("related"), "related"),
        supersedes=_link_target(raw.get("supersedes"), "supersedes"),
        superseded_by=_link_target(raw.get("superseded_by"), "superseded_by"),
    )

    if datetime.fromisoformat(metadata.updated) < datetime.fromisoformat(metadata.created):
        raise SchemaError("Field 'updated' must not be earlier than 'created'.")

    if relative_path:
        check_type_matches_path(metadata.type, relative_path)
    return metadata


def check_type_matches_path(page_type: str, relative_path: str) -> None:
    """A page's declared type must match the folder it lives in."""
    spec = PAGE_TYPES[page_type]
    parts = relative_path.strip("/").split("/")
    folder = parts[0] if len(parts) > 1 else ""
    if spec.folder != folder:
        expected = spec.folder or "the Vault root"
        actual = folder or "the Vault root"
        raise SchemaError(
            f"A page of type '{page_type}' belongs in {expected}, but this one is in {actual}."
        )


def folder_for(page_type: str) -> str:
    return PAGE_TYPES[page_type].folder


def generate_id(page_type: str, *, relative_path: str, title: str, created: str) -> str:
    """Derive a stable, unique-in-practice page id.

    The id is a pure function of the page's path, title and creation time, so
    the same page always yields the same id and a rebuild is reproducible.
    """
    if page_type not in PAGE_TYPES:
        raise SchemaError(f"Unknown page type {page_type!r}.")
    prefix = PAGE_TYPES[page_type].id_prefix
    seed = f"{page_type}|{relative_path}|{title}|{created}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10]
    day = created[:10].replace("-", "")
    return f"{prefix}-{day}-{digest}"


def content_hash(text: str) -> str:
    """Short content hash recorded in the changelog."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
