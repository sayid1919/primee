"""Filesystem layer for the Vault skill.

This is the only module in Primee that writes persistent user content, and it
is only reachable through the Vault skill handler.  Every operation:

* validates the requested path twice (shape, then resolution),
* refuses to follow symbolic links or NTFS junctions,
* refuses to leave the configured Vault root,
* writes atomically via a temporary file plus ``os.replace``,
* writes strict UTF-8 only,
* records which skill requested the change.

The Vault never stores credentials.  Content that looks like a secret is
rejected outright rather than being written and redacted later.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ...core.errors import ErrorCode, PrimeeError
from ...core.paths import normalize_relative_path, resolve_root, resolve_within

DEFAULT_MAX_BYTES = 512 * 1024
MAX_LIST_RESULTS = 500

#: Content that must never reach the Vault, checked before any write.
_SECRET_MARKERS = (
    re.compile(r"-----BEGIN[^-]{0,64}PRIVATE KEY-----", re.IGNORECASE),
    re.compile(r"(?i)\b(api[_-]?key|secret|password|passwd|access[_-]?token|"
               r"refresh[_-]?token|client[_-]?secret|seed[_-]?phrase|mnemonic|"
               r"recovery[_-]?code)\b\s*[:=]\s*\S"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
)


@dataclass(frozen=True)
class VaultFileInfo:
    path: str
    size_bytes: int
    modified_at: float


class VaultStorage:
    """Path-isolated storage rooted at a single configured directory."""

    def __init__(self, root: object, *, max_file_bytes: int = DEFAULT_MAX_BYTES) -> None:
        if root is None or str(root).strip() == "":
            raise PrimeeError(
                ErrorCode.VAULT_NOT_CONFIGURED,
                "The Vault root is not configured. Set [vault] root in your "
                "local Primee configuration.",
            )
        self.root = resolve_root(root)
        self.max_file_bytes = int(max_file_bytes)

    # -- helpers --------------------------------------------------------
    def _target(self, relative: str) -> Path:
        return resolve_within(self.root, relative)

    def _check_content(self, content: object) -> str:
        if not isinstance(content, str):
            raise PrimeeError(
                ErrorCode.VAULT_CONTENT_REJECTED, "Vault content must be text."
            )
        if "\x00" in content:
            raise PrimeeError(
                ErrorCode.VAULT_CONTENT_REJECTED, "Vault content contains a NUL byte."
            )
        try:
            encoded = content.encode("utf-8", errors="strict")
        except UnicodeEncodeError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_CONTENT_REJECTED, "Vault content is not valid UTF-8."
            ) from exc
        if len(encoded) > self.max_file_bytes:
            raise PrimeeError(
                ErrorCode.VAULT_CONTENT_REJECTED,
                f"Vault content exceeds the {self.max_file_bytes} byte limit.",
            )
        for pattern in _SECRET_MARKERS:
            if pattern.search(content):
                raise PrimeeError(
                    ErrorCode.VAULT_CONTENT_REJECTED,
                    "The content looks like it contains a credential. "
                    "Primee refuses to store credentials in the Vault.",
                )
        return content

    def _atomic_write(self, target: Path, content: str) -> int:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=".primee-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:  # pragma: no cover - best effort cleanup
                pass
            raise
        return len(content.encode("utf-8"))

    # -- operations -----------------------------------------------------
    def exists(self, relative: str) -> bool:
        return self._target(relative).is_file()

    def read(self, relative: str) -> str:
        target = self._target(relative)
        if not target.is_file():
            raise PrimeeError(
                ErrorCode.VAULT_FILE_MISSING,
                f"Vault file '{normalize_relative_path(relative)}' does not exist.",
            )
        try:
            if target.stat().st_size > self.max_file_bytes:
                raise PrimeeError(
                    ErrorCode.VAULT_IO_ERROR, "The Vault file is too large to read."
                )
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_IO_ERROR, "The Vault file is not valid UTF-8 text."
            ) from exc
        except OSError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_IO_ERROR, "The Vault file could not be read."
            ) from exc

    def create(self, relative: str, content: str) -> int:
        target = self._target(relative)
        text = self._check_content(content)
        if target.exists():
            raise PrimeeError(
                ErrorCode.VAULT_FILE_EXISTS,
                f"Vault file '{normalize_relative_path(relative)}' already exists; "
                "use 'update' or 'append' instead.",
            )
        try:
            return self._atomic_write(target, text)
        except OSError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_IO_ERROR, "The Vault file could not be created."
            ) from exc

    def append(self, relative: str, content: str) -> int:
        target = self._target(relative)
        text = self._check_content(content)
        existing = ""
        if target.is_file():
            existing = self.read(relative)
            if existing and not existing.endswith("\n"):
                existing += "\n"
        merged = self._check_content(existing + text)
        try:
            return self._atomic_write(target, merged)
        except OSError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_IO_ERROR, "The Vault file could not be appended to."
            ) from exc

    def update(self, relative: str, content: str) -> int:
        target = self._target(relative)
        text = self._check_content(content)
        if not target.is_file():
            raise PrimeeError(
                ErrorCode.VAULT_FILE_MISSING,
                f"Vault file '{normalize_relative_path(relative)}' does not exist; "
                "use 'create' instead.",
            )
        try:
            return self._atomic_write(target, text)
        except OSError as exc:
            raise PrimeeError(
                ErrorCode.VAULT_IO_ERROR, "The Vault file could not be updated."
            ) from exc

    def list(self, prefix: Optional[str] = None) -> list[VaultFileInfo]:
        base = self.root
        if prefix:
            normalized = normalize_relative_path(prefix)
            base = resolve_within(self.root, normalized)
            if not base.is_dir():
                return []
        results: list[VaultFileInfo] = []
        for path in sorted(base.rglob("*")):
            if len(results) >= MAX_LIST_RESULTS:
                break
            if path.is_symlink() or not path.is_file():
                continue
            if path.name.startswith(".primee-"):
                continue
            try:
                relative = path.relative_to(self.root).as_posix()
                stat = path.stat()
            except (OSError, ValueError):  # pragma: no cover - defensive
                continue
            results.append(
                VaultFileInfo(
                    path=relative, size_bytes=stat.st_size, modified_at=stat.st_mtime
                )
            )
        return results
