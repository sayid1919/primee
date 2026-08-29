"""The Vault operation catalogue and its permission mapping.

Primee deliberately exposes a fixed list of named memory operations.  There is
no generic "write this file" call: every operation states what it does, which
permission it needs, and whether it changes anything.

This mapping is the single source of truth shared by Primee Core (which gates
the call) and the Vault skill (which performs it), so the two can never drift
apart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

READ = "vault.read"
LIST = "vault.list"
CREATE = "vault.create"
APPEND = "vault.append"
UPDATE = "vault.update"
INIT = "vault.init"
INDEX = "vault.index"


@dataclass(frozen=True)
class OperationSpec:
    name: str
    permission: str
    mutating: bool
    description: str


_OPERATIONS: tuple[OperationSpec, ...] = (
    # -- structure ----------------------------------------------------------
    OperationSpec("init", INIT, True, "Create the Vault skeleton at an approved path."),
    OperationSpec("validate", READ, False, "Check the Vault's structure, pages and links."),
    # -- reading ------------------------------------------------------------
    OperationSpec("read", READ, False, "Read one page and its validated metadata."),
    OperationSpec("list", LIST, False, "List file paths under the Vault or a folder."),
    OperationSpec("search_text", LIST, False, "Find pages whose text matches a query."),
    OperationSpec("search_tag", LIST, False, "Find pages carrying a tag."),
    OperationSpec("search_metadata", LIST, False, "Find pages by a frontmatter field value."),
    OperationSpec("recent", LIST, False, "List the most recently updated pages."),
    OperationSpec("backlinks", LIST, False, "Find every page linking to a target."),
    OperationSpec("validate_links", LIST, False, "Report dangling and ambiguous wikilinks."),
    # -- writing ------------------------------------------------------------
    OperationSpec("create_raw", CREATE, True, "Capture a new, immutable raw note."),
    OperationSpec("amend_raw", CREATE, True, "Add an amendment linked to an existing raw note."),
    OperationSpec("write_wiki", CREATE, True, "Create a wiki topic page."),
    OperationSpec("update_wiki", UPDATE, True, "Update an existing wiki topic page."),
    OperationSpec("publish_output", CREATE, True, "Publish a new dated output."),
    OperationSpec("revise_output", CREATE, True, "Publish a linked revision of an existing output."),
    OperationSpec("rebuild_index", INDEX, True, "Regenerate INDEX.md from the pages themselves."),
    OperationSpec("append_change", APPEND, True, "Append one event to the changelog."),
    # -- Step One file operations, kept for compatibility --------------------
    OperationSpec("create", CREATE, True, "Create a plain file inside the Vault."),
    OperationSpec("append", APPEND, True, "Append to a plain file inside the Vault."),
    OperationSpec("update", UPDATE, True, "Replace a plain file inside the Vault."),
)

OPERATIONS: dict[str, OperationSpec] = {spec.name: spec for spec in _OPERATIONS}
OPERATION_NAMES = tuple(sorted(OPERATIONS))
MUTATING_OPERATIONS = frozenset(name for name, s in OPERATIONS.items() if s.mutating)

#: Every permission the Vault skill must declare in its SKILL.md.
REQUIRED_PERMISSIONS = frozenset(spec.permission for spec in _OPERATIONS)


def permission_for(operation: object, inputs: Mapping[str, Any] | None = None) -> str | None:
    """Return the permission an operation needs, or ``None`` if unknown.

    ``inputs`` is accepted so that a future operation can vary its permission
    with its arguments; today every operation maps statically, which keeps the
    gate easy to audit.
    """
    spec = OPERATIONS.get(str(operation or "").strip().lower())
    return spec.permission if spec else None


def is_mutating(operation: object) -> bool:
    spec = OPERATIONS.get(str(operation or "").strip().lower())
    return bool(spec and spec.mutating)


def describe_operations() -> list[dict]:
    return [
        {
            "operation": spec.name,
            "permission": spec.permission,
            "mutating": spec.mutating,
            "description": spec.description,
        }
        for spec in sorted(_OPERATIONS, key=lambda s: s.name)
    ]
