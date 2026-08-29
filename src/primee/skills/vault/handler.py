"""Vault skill handler — Primee's only persistent-memory gateway.

The handler receives an already permission-checked request from Primee Core and
performs it against :class:`~primee.memory.service.MemoryService`.  It does no
permission logic of its own: that lives in Primee Core, so no skill can reach
memory by calling this module directly with a forged context.
"""

from __future__ import annotations

from typing import Any

from primee.core.context import SkillContext
from primee.core.errors import ErrorCode, PrimeeError
from primee.core.paths import normalize_relative_path
from primee.core.result_types import SkillResult
from primee.memory.operations import OPERATIONS, is_mutating
from primee.memory.schema import CONTENT_FOLDERS

SKILL_NAME = "vault"

# Plain file operations kept from Step One, for content that is not a page.
FILE_READ = "read_file"
LEGACY_FILE_OPS = ("create", "append", "update")


def run(context: SkillContext) -> SkillResult:
    operation = str(context.input("operation", "")).strip().lower()
    if operation not in OPERATIONS:
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault received an unsupported operation.",
            ErrorCode.INVALID_INPUT,
            "Operation must be one of: " + ", ".join(sorted(OPERATIONS)) + ".",
        )

    requested_by = str(context.input("requested_by", context.skill_name) or SKILL_NAME)

    # 'init' is the operation that CREATES the Vault, so it cannot require a
    # working one. It runs against the initializer directly.
    if operation == "init":
        return _initialize(context, requested_by)

    memory = context.storage
    if memory is None:
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault is not available.",
            ErrorCode.VAULT_NOT_CONFIGURED,
            "Primee Core did not provide Vault memory for this request. Set "
            "[vault] root (or PRIMEE_VAULT_PATH) and create the Vault with the "
            "'init' operation first.",
        )
    metadata: dict[str, Any] = context.input("metadata", {}) or {}
    if not isinstance(metadata, dict):
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault received malformed page metadata.",
            ErrorCode.INVALID_INPUT,
            "'metadata' must be a mapping of page fields.",
        )

    # 'init' understands dry-run itself and reports exactly what it would
    # create, which is more useful than a flat refusal.
    if context.dry_run and is_mutating(operation) and operation != "init":
        return SkillResult.fail(
            SKILL_NAME,
            f"Dry run: '{operation}' was not performed.",
            ErrorCode.DRY_RUN_BLOCKED,
            "Primee is running in dry-run mode, so nothing was written.",
        )

    try:
        return _dispatch(context, memory, operation, metadata, requested_by)
    except PrimeeError as exc:
        return SkillResult.fail(
            SKILL_NAME, "The Vault refused the request.", exc.code, exc.message
        )


def _initialize(context: SkillContext, requested_by: str) -> SkillResult:
    """Create the Vault skeleton. This is the one operation that bootstraps."""
    from primee.memory.initializer import initialize_vault

    root = context.input("root", None) or context.config.vault.root
    try:
        plan = initialize_vault(
            root,
            context.clock,
            dry_run=bool(context.dry_run or context.input("dry_run", False)),
            adopt_non_empty=bool(context.input("adopt_non_empty", False)),
        )
    except PrimeeError as exc:
        return SkillResult.fail(
            SKILL_NAME, "The Vault could not be initialised.", exc.code, exc.message
        )
    return _ok("init", plan.describe(), {**plan.to_dict(), "requested_by": requested_by})


def _dispatch(context, memory, operation, metadata, requested_by) -> SkillResult:
    # -- structure ------------------------------------------------------
    if operation == "validate":
        result = memory.validate_vault()
        if not result.ok:
            return SkillResult.fail(
                SKILL_NAME,
                result.message,
                ErrorCode.VAULT_CONTENT_REJECTED,
                "The Vault did not pass validation; see structured_data for the details.",
                structured_data={**result.to_dict(), "requested_by": requested_by},
            )
        return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})

    # -- reading --------------------------------------------------------
    if operation == "read":
        path = normalize_relative_path(context.input("path", ""))
        # Pages live in raw/, wiki/ and outputs/ and must carry frontmatter.
        # Anything else in the Vault is read as plain text.
        is_page = path.endswith(".md") and path.split("/")[0] in CONTENT_FOLDERS
        if not is_page:
            content = memory.storage.read(path)
            return _ok(
                operation,
                f"Read '{path}' from the Vault.",
                {"path": path, "content": content, "body": content, "requested_by": requested_by},
            )
        result = memory.read_page(path)
        return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})

    if operation == "list":
        prefix = context.input("prefix", "") or ""
        entries = memory.storage.list(prefix or None)
        paths = [entry.path for entry in entries]
        where = f"'{prefix}'" if prefix else "the Vault root"
        return _ok(
            operation,
            f"Found {len(paths)} file(s) under {where}.",
            {"prefix": prefix, "paths": paths, "requested_by": requested_by},
        )

    if operation in ("search_text", "search_tag", "search_metadata", "recent"):
        mode = operation.replace("search_", "") if operation != "recent" else "recent"
        result = memory.search(
            mode,
            query=context.input("query", ""),
            tag=context.input("tag", ""),
            field=context.input("field", ""),
            value=context.input("value", ""),
            folder=context.input("folder", None),
            limit=context.input("limit", 20),
        )
        return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})

    if operation == "backlinks":
        result = memory.backlinks(str(context.input("target", "")))
        return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})

    if operation == "validate_links":
        result = memory.validate_links()
        return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})

    # -- writing --------------------------------------------------------
    body = context.input("content", "") or ""

    if operation == "create_raw":
        result = memory.create_raw(
            title=metadata.get("title", ""),
            body=body,
            summary=metadata.get("summary", ""),
            tags=metadata.get("tags", ()),
            source=metadata.get("source", ""),
            source_type=metadata.get("source_type", "manual"),
            sensitivity=metadata.get("sensitivity", "private"),
            related=metadata.get("related", ()),
            captured_at=metadata.get("captured_at", ""),
            actor=requested_by,
        )
    elif operation == "amend_raw":
        result = memory.amend_raw(
            target=str(context.input("target", "") or metadata.get("target", "")),
            body=body,
            summary=metadata.get("summary", ""),
            title=metadata.get("title", ""),
            tags=metadata.get("tags", ()),
            actor=requested_by,
        )
    elif operation in ("write_wiki", "update_wiki"):
        result = memory.write_wiki(
            title=metadata.get("title", ""),
            body=body,
            summary=metadata.get("summary", ""),
            slug=metadata.get("slug", ""),
            tags=metadata.get("tags", ()),
            related=metadata.get("related", ()),
            status=metadata.get("status", "active"),
            sensitivity=metadata.get("sensitivity", "private"),
            source=metadata.get("source", ""),
            allow_update=(operation == "update_wiki"),
            actor=requested_by,
        )
    elif operation == "publish_output":
        result = memory.publish_output(
            title=metadata.get("title", ""),
            body=body,
            summary=metadata.get("summary", ""),
            output_type=metadata.get("output_type", "output_report"),
            tags=metadata.get("tags", ()),
            related=metadata.get("related", ()),
            status=metadata.get("status", "final"),
            sensitivity=metadata.get("sensitivity", "private"),
            source=metadata.get("source", ""),
            date_hint=str(metadata.get("date_hint", "")),
            actor=requested_by,
        )
    elif operation == "revise_output":
        result = memory.revise_output(
            target=str(context.input("target", "") or metadata.get("target", "")),
            body=body,
            summary=metadata.get("summary", ""),
            title=metadata.get("title", ""),
            tags=metadata.get("tags", ()),
            status=metadata.get("status", "final"),
            actor=requested_by,
        )
    elif operation == "rebuild_index":
        result = memory.rebuild_index(actor=requested_by)
    elif operation == "append_change":
        from primee.memory.changelog import ChangeEvent

        memory.append_change(
            ChangeEvent(
                timestamp=memory.now(),
                operation=str(metadata.get("event", "note")),
                page_id=str(metadata.get("page_id", "")),
                path=str(metadata.get("path", "")),
                actor=requested_by,
                approval_state=str(metadata.get("approval_state", "auto")),
                result=str(metadata.get("result", "ok")),
            )
        )
        return _ok(
            operation,
            "Appended one event to the Vault changelog.",
            {"requested_by": requested_by},
        )
    elif operation in LEGACY_FILE_OPS:
        return _file_operation(memory, operation, context, body, requested_by)
    else:  # pragma: no cover - the catalogue and this dispatch stay in sync
        raise PrimeeError(
            ErrorCode.INVALID_INPUT, f"Operation '{operation}' is not implemented."
        )

    return _ok(operation, result.message, {**result.to_dict(), "requested_by": requested_by})


def _file_operation(memory, operation, context, body, requested_by) -> SkillResult:
    """The Step One plain-file operations, for content that is not a page."""
    path = memory._guard_reserved(context.input("path", ""))
    if not isinstance(body, str):
        raise PrimeeError(
            ErrorCode.INVALID_INPUT, f"Operation '{operation}' requires text content."
        )
    if operation == "create":
        written = memory.storage.create(path, body)
    elif operation == "append":
        written = memory.storage.append(path, body)
    else:
        written = memory.storage.update(path, body)
    memory.invalidate()
    return _ok(
        operation,
        f"Vault {operation} on '{path}' completed ({written} bytes) for skill '{requested_by}'.",
        {"path": path, "bytes_written": written, "requested_by": requested_by},
    )


def _ok(operation: str, message: str, data: dict) -> SkillResult:
    return SkillResult.ok(
        SKILL_NAME, message, structured_data={"operation": operation, **data}
    )
