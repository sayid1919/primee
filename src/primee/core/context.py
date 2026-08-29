"""The execution context handed to a skill handler.

A handler receives data and read-only capabilities, never raw filesystem or
network access.  Persistent writes are *proposed* in the result and performed by
Primee Core through the Vault skill.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .clock import Clock, timestamp_iso, today_iso
from .config import PrimeeConfig
from .errors import ErrorCode


@dataclass(frozen=True)
class VaultReadResult:
    ok: bool
    found: bool
    content: Optional[str] = None
    error_code: Optional[str] = None
    message: str = ""


@dataclass(frozen=True)
class VaultAccess:
    """Read side of the Vault, routed through the Vault skill.

    Skills never touch the filesystem.  Every call here is a full Primee Core
    round trip: permission check, approval gate, Vault skill, audit event.
    """

    _reader: Callable[[str], VaultReadResult]
    _lister: Callable[[str], list[str]]

    def read(self, path: str) -> VaultReadResult:
        return self._reader(path)

    def list(self, prefix: str = "") -> list[str]:
        return self._lister(prefix)

    def exists(self, path: str) -> bool:
        return self.read(path).found


class DeniedVaultAccess(VaultAccess):
    """Placeholder used when a skill did not declare ``vault.read``."""

    def __init__(self) -> None:
        super().__init__(_reader=self._denied, _lister=lambda prefix: [])

    @staticmethod
    def _denied(path: str) -> VaultReadResult:
        return VaultReadResult(
            ok=False,
            found=False,
            error_code=ErrorCode.PERMISSION_NOT_DECLARED,
            message="This skill did not declare the 'vault.read' permission.",
        )


@dataclass(frozen=True)
class SkillContext:
    """Everything a handler is allowed to see."""

    skill_name: str
    request: str
    inputs: dict[str, Any]
    config: PrimeeConfig
    clock: Clock
    connectors: Any
    vault: VaultAccess
    dry_run: bool = False
    storage: Any = field(default=None, repr=False)

    def today(self) -> str:
        return today_iso(self.clock)

    def now_iso(self) -> str:
        return timestamp_iso(self.clock)

    def input(self, name: str, default: Any = None) -> Any:
        value = self.inputs.get(name, default)
        return default if value is None else value
