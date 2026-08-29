"""The standard structured result every Primee skill handler must return.

A handler never performs a persistent write or an external side effect itself.
It *proposes* them, and Primee Core decides — through the permission layer, the
approval gate and the Vault skill — whether they happen.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .errors import ALL_ERROR_CODES, ErrorCode, ResultValidationError
from .redaction import redact_structure, redact_text

SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

#: Vault operations a skill may propose.  Deleting is deliberately absent in
#: Step One: Primee has no irreversible-delete capability yet.
VAULT_OPERATIONS = frozenset({"create", "append", "update"})

MAX_SUMMARY_LENGTH = 2000
MAX_WARNINGS = 20
MAX_VAULT_WRITES = 20
MAX_EXTERNAL_ACTIONS = 20
MAX_CONTENT_BYTES = 512 * 1024


@dataclass(frozen=True)
class VaultWrite:
    """A persistent write a skill would like the Vault skill to perform."""

    path: str
    operation: str
    content: str
    reason: str = ""

    def validate(self) -> None:
        if self.operation not in VAULT_OPERATIONS:
            raise ResultValidationError(
                f"Unsupported vault operation {self.operation!r}."
            )
        if not isinstance(self.path, str) or not self.path.strip():
            raise ResultValidationError("Vault write path must be a non-empty string.")
        if not isinstance(self.content, str):
            raise ResultValidationError("Vault write content must be a string.")
        if len(self.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ResultValidationError("Vault write content is too large.")
        if not isinstance(self.reason, str):
            raise ResultValidationError("Vault write reason must be a string.")

    @property
    def permission(self) -> str:
        return f"vault.{self.operation}"

    def describe(self) -> dict:
        """A summary safe for logs: metadata only, never the file content."""
        return {
            "path": self.path,
            "operation": self.operation,
            "content_bytes": len(self.content.encode("utf-8")),
            "reason": redact_text(self.reason),
        }


@dataclass(frozen=True)
class ExternalAction:
    """An action with an effect outside Primee.

    Step One never executes these.  They are returned to the user for explicit
    approval and recorded as pending.
    """

    action: str
    target: str
    description: str
    required_permission: str
    payload: dict = field(default_factory=dict)

    def validate(self) -> None:
        for name in ("action", "target", "description", "required_permission"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ResultValidationError(
                    f"External action field {name!r} must be a non-empty string."
                )
        if not isinstance(self.payload, dict):
            raise ResultValidationError("External action payload must be a mapping.")

    def describe(self) -> dict:
        return {
            "action": self.action,
            "target": redact_text(self.target),
            "description": redact_text(self.description),
            "required_permission": self.required_permission,
            "payload": redact_structure(self.payload),
        }


@dataclass
class SkillResult:
    """The single result shape every handler returns."""

    success: bool
    skill_name: str
    summary: str
    structured_data: dict = field(default_factory=dict)
    proposed_vault_writes: list[VaultWrite] = field(default_factory=list)
    proposed_external_actions: list[ExternalAction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error_code: Optional[str] = None
    sanitized_error_message: Optional[str] = None

    # -- constructors ---------------------------------------------------
    @classmethod
    def ok(cls, skill_name: str, summary: str, **kwargs: Any) -> "SkillResult":
        return cls(success=True, skill_name=skill_name, summary=summary, **kwargs)

    @classmethod
    def fail(
        cls,
        skill_name: str,
        summary: str,
        error_code: str,
        message: str,
        **kwargs: Any,
    ) -> "SkillResult":
        return cls(
            success=False,
            skill_name=skill_name,
            summary=summary,
            error_code=error_code,
            sanitized_error_message=redact_text(message),
            **kwargs,
        )

    # -- validation -----------------------------------------------------
    def validate(self) -> "SkillResult":
        if not isinstance(self.success, bool):
            raise ResultValidationError("Result field 'success' must be a boolean.")
        if not isinstance(self.skill_name, str) or not SKILL_NAME_RE.match(self.skill_name):
            raise ResultValidationError(
                "Result field 'skill_name' must be a lowercase identifier."
            )
        if not isinstance(self.summary, str) or not self.summary.strip():
            raise ResultValidationError("Result field 'summary' must be a non-empty string.")
        if len(self.summary) > MAX_SUMMARY_LENGTH:
            raise ResultValidationError("Result field 'summary' is too long.")
        if not isinstance(self.structured_data, dict):
            raise ResultValidationError("Result field 'structured_data' must be a mapping.")
        try:
            json.dumps(self.structured_data, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ResultValidationError(
                "Result field 'structured_data' must be JSON serialisable."
            ) from exc

        if not isinstance(self.proposed_vault_writes, list):
            raise ResultValidationError("Result field 'proposed_vault_writes' must be a list.")
        if len(self.proposed_vault_writes) > MAX_VAULT_WRITES:
            raise ResultValidationError("Too many proposed vault writes.")
        for write in self.proposed_vault_writes:
            if not isinstance(write, VaultWrite):
                raise ResultValidationError(
                    "Every proposed vault write must be a VaultWrite instance."
                )
            write.validate()

        if not isinstance(self.proposed_external_actions, list):
            raise ResultValidationError(
                "Result field 'proposed_external_actions' must be a list."
            )
        if len(self.proposed_external_actions) > MAX_EXTERNAL_ACTIONS:
            raise ResultValidationError("Too many proposed external actions.")
        for action in self.proposed_external_actions:
            if not isinstance(action, ExternalAction):
                raise ResultValidationError(
                    "Every proposed external action must be an ExternalAction instance."
                )
            action.validate()

        if not isinstance(self.warnings, list) or not all(
            isinstance(item, str) for item in self.warnings
        ):
            raise ResultValidationError("Result field 'warnings' must be a list of strings.")
        if len(self.warnings) > MAX_WARNINGS:
            raise ResultValidationError("Too many warnings.")

        if self.success:
            if self.error_code not in (None, ErrorCode.OK):
                raise ResultValidationError(
                    "A successful result must not carry an error code."
                )
            if self.sanitized_error_message:
                raise ResultValidationError(
                    "A successful result must not carry an error message."
                )
        else:
            if self.error_code not in ALL_ERROR_CODES or self.error_code in (
                None,
                ErrorCode.OK,
            ):
                raise ResultValidationError(
                    "A failed result must carry a known non-OK error code."
                )
            if not isinstance(self.sanitized_error_message, str) or not (
                self.sanitized_error_message.strip()
            ):
                raise ResultValidationError(
                    "A failed result must carry a sanitized error message."
                )

        # Redaction is enforced, not merely requested.
        self.summary = redact_text(self.summary)
        self.warnings = [redact_text(item) for item in self.warnings]
        if self.sanitized_error_message:
            self.sanitized_error_message = redact_text(self.sanitized_error_message)
        return self

    # -- serialisation --------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "skill_name": self.skill_name,
            "summary": self.summary,
            "structured_data": self.structured_data,
            "proposed_vault_writes": [w.describe() for w in self.proposed_vault_writes],
            "proposed_external_actions": [
                a.describe() for a in self.proposed_external_actions
            ],
            "warnings": list(self.warnings),
            "error_code": self.error_code,
            "sanitized_error_message": self.sanitized_error_message,
        }
