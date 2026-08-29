"""Vault skill handler - Primee's only persistent-storage gateway.

The handler receives an already permission-checked request from Primee Core and
performs it against :class:`~primee.skills.vault.storage.VaultStorage`.  It does
no permission logic of its own: that lives in Primee Core so that no skill can
reach storage by calling this module directly with a forged context.
"""

from __future__ import annotations

from primee.core.context import SkillContext
from primee.core.errors import ErrorCode, PrimeeError
from primee.core.paths import normalize_relative_path
from primee.core.result_types import SkillResult

SKILL_NAME = "vault"

READ = "read"
LIST = "list"
CREATE = "create"
APPEND = "append"
UPDATE = "update"
OPERATIONS = (READ, LIST, CREATE, APPEND, UPDATE)


def run(context: SkillContext) -> SkillResult:
    operation = str(context.input("operation", "")).strip().lower()
    if operation not in OPERATIONS:
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault received an unsupported operation.",
            ErrorCode.INVALID_INPUT,
            f"Operation must be one of: {', '.join(OPERATIONS)}.",
        )

    storage = context.storage
    if storage is None:
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault is not available.",
            ErrorCode.VAULT_NOT_CONFIGURED,
            "Primee Core did not provide Vault storage for this request. "
            "Check that [vault] root is set in your local configuration.",
        )

    requested_by = str(context.input("requested_by", context.skill_name) or SKILL_NAME)

    try:
        if operation == LIST:
            return _list(context, storage, requested_by)
        if operation == READ:
            return _read(context, storage, requested_by)
        return _write(context, storage, operation, requested_by)
    except PrimeeError as exc:
        return SkillResult.fail(
            SKILL_NAME, "The Vault refused the request.", exc.code, exc.message
        )


def _read(context: SkillContext, storage, requested_by: str) -> SkillResult:
    path = normalize_relative_path(context.input("path", ""))
    content = storage.read(path)
    return SkillResult.ok(
        SKILL_NAME,
        f"Read '{path}' from the Vault.",
        structured_data={
            "operation": READ,
            "path": path,
            "content": content,
            "bytes": len(content.encode("utf-8")),
            "requested_by": requested_by,
        },
    )


def _list(context: SkillContext, storage, requested_by: str) -> SkillResult:
    prefix = context.input("prefix", "") or ""
    entries = storage.list(prefix or None)
    paths = [entry.path for entry in entries]
    where = f"'{prefix}'" if prefix else "the Vault root"
    return SkillResult.ok(
        SKILL_NAME,
        f"Found {len(paths)} file(s) under {where}.",
        structured_data={
            "operation": LIST,
            "prefix": prefix,
            "paths": paths,
            "requested_by": requested_by,
        },
    )


def _write(context: SkillContext, storage, operation: str, requested_by: str) -> SkillResult:
    path = normalize_relative_path(context.input("path", ""))
    content = context.input("content", None)
    if not isinstance(content, str):
        return SkillResult.fail(
            SKILL_NAME,
            "The Vault write was rejected.",
            ErrorCode.INVALID_INPUT,
            f"Operation '{operation}' requires text content.",
        )

    if context.dry_run:
        return SkillResult.fail(
            SKILL_NAME,
            f"Dry run: '{operation}' on '{path}' was not performed.",
            ErrorCode.DRY_RUN_BLOCKED,
            "Primee is running in dry-run mode, so no file was changed.",
        )

    if operation == CREATE:
        written = storage.create(path, content)
    elif operation == APPEND:
        written = storage.append(path, content)
    else:
        written = storage.update(path, content)

    return SkillResult.ok(
        SKILL_NAME,
        f"Vault {operation} on '{path}' completed ({written} bytes) for skill '{requested_by}'.",
        structured_data={
            "operation": operation,
            "path": path,
            "bytes_written": written,
            "requested_by": requested_by,
        },
    )
