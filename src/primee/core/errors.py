"""Stable error codes and sanitized error handling for Primee Core.

Primee never leaks raw exception text to the user, to reports, or to the audit
log.  Every failure is expressed as a stable machine readable ``error_code``
plus a short human readable message that has passed through redaction.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


class ErrorCode:
    """Stable error codes.  These strings are part of the public contract."""

    OK = "OK"

    # Loading / manifests
    FRONTMATTER_INVALID = "FRONTMATTER_INVALID"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    DUPLICATE_SKILL_NAME = "DUPLICATE_SKILL_NAME"
    HANDLER_INVALID = "HANDLER_INVALID"
    HANDLER_FAILED = "HANDLER_FAILED"
    SKILLS_ROOT_MISSING = "SKILLS_ROOT_MISSING"

    # Routing
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    UNKNOWN_COMMAND = "UNKNOWN_COMMAND"
    NO_MATCHING_SKILL = "NO_MATCHING_SKILL"
    AMBIGUOUS_REQUEST = "AMBIGUOUS_REQUEST"
    EMPTY_REQUEST = "EMPTY_REQUEST"

    # Permissions / approval
    PERMISSION_UNKNOWN = "PERMISSION_UNKNOWN"
    PERMISSION_NOT_DECLARED = "PERMISSION_NOT_DECLARED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    APPROVAL_DENIED = "APPROVAL_DENIED"
    DRY_RUN_BLOCKED = "DRY_RUN_BLOCKED"

    # Results
    RESULT_INVALID = "RESULT_INVALID"

    # Vault
    VAULT_NOT_CONFIGURED = "VAULT_NOT_CONFIGURED"
    VAULT_PATH_REJECTED = "VAULT_PATH_REJECTED"
    VAULT_FILE_EXISTS = "VAULT_FILE_EXISTS"
    VAULT_FILE_MISSING = "VAULT_FILE_MISSING"
    VAULT_IO_ERROR = "VAULT_IO_ERROR"
    VAULT_CONTENT_REJECTED = "VAULT_CONTENT_REJECTED"

    # Connectors
    CONNECTOR_NOT_CONFIGURED = "CONNECTOR_NOT_CONFIGURED"
    CONNECTOR_ERROR = "CONNECTOR_ERROR"

    # Inputs / config
    INVALID_INPUT = "INVALID_INPUT"
    CONFIG_INVALID = "CONFIG_INVALID"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"

    UNEXPECTED = "UNEXPECTED"


ALL_ERROR_CODES = frozenset(
    value
    for key, value in vars(ErrorCode).items()
    if not key.startswith("_") and isinstance(value, str)
)


class PrimeeError(Exception):
    """Base class for every Primee Core failure.

    ``message`` must already be safe to show to a user.  ``detail`` is a small
    structured mapping that is redacted again before it reaches the audit log.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code if code in ALL_ERROR_CODES else ErrorCode.UNEXPECTED
        self.message = message
        self.detail = dict(detail or {})

    def sanitized_message(self) -> str:
        from .redaction import redact_text

        return redact_text(self.message)

    def sanitized_detail(self) -> dict:
        from .redaction import redact_structure

        return redact_structure(self.detail)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"PrimeeError(code={self.code!r}, message={self.message!r})"


class FrontmatterError(PrimeeError):
    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.FRONTMATTER_INVALID, message, detail=detail)


class ManifestError(PrimeeError):
    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.MANIFEST_INVALID, message, detail=detail)


class ResultValidationError(PrimeeError):
    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.RESULT_INVALID, message, detail=detail)


class PathRejectedError(PrimeeError):
    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.VAULT_PATH_REJECTED, message, detail=detail)


class PermissionDeniedError(PrimeeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = ErrorCode.PERMISSION_DENIED,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> None:
        super().__init__(code, message, detail=detail)


class ConfigError(PrimeeError):
    def __init__(self, message: str, *, detail: Optional[Mapping[str, Any]] = None) -> None:
        super().__init__(ErrorCode.CONFIG_INVALID, message, detail=detail)
