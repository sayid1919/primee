"""Re-export of the Vault operation catalogue for Primee Core.

The catalogue itself lives in :mod:`primee.memory.operations`, next to the code
that implements it.  Core needs the operation-to-permission mapping in order to
gate a call *before* the Vault skill runs, and importing it from one place keeps
the gate and the implementation from ever disagreeing.
"""

from __future__ import annotations

from ..memory.operations import (  # noqa: F401
    MUTATING_OPERATIONS,
    OPERATION_NAMES,
    OPERATIONS,
    REQUIRED_PERMISSIONS,
    describe_operations,
    is_mutating,
    permission_for,
)

__all__ = [
    "MUTATING_OPERATIONS",
    "OPERATIONS",
    "OPERATION_NAMES",
    "REQUIRED_PERMISSIONS",
    "describe_operations",
    "is_mutating",
    "permission_for",
]
