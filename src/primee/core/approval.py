"""Approval gates for permissions whose policy mode is ``approval``.

Step One ships three gates:

* :class:`DenyAllApprovalGate` - the default.  Nothing that needs approval runs.
* :class:`PreApprovedGate` - the operator explicitly pre-approved a set of
  permissions for a single run (``--approve vault.update`` on the CLI).
* :class:`CallbackApprovalGate` - a seam for a future interactive prompt or HUD.

A gate can only ever *narrow* what the permission policy already allowed; it can
never grant a permission the policy denies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

GRANTED = "granted"
DENIED = "denied"
NOT_REQUESTED = "not_requested"
DRY_RUN = "dry_run"


@dataclass(frozen=True)
class ApprovalRequest:
    skill_name: str
    permission: str
    action: str
    description: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalOutcome:
    granted: bool
    state: str
    reason: str


class ApprovalGate:
    """Base class.  The default behaviour is to refuse."""

    def request(self, request: ApprovalRequest) -> ApprovalOutcome:  # pragma: no cover
        raise NotImplementedError


class DenyAllApprovalGate(ApprovalGate):
    def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        return ApprovalOutcome(
            granted=False,
            state=DENIED,
            reason=(
                "This action needs explicit approval and no approval was given "
                "for this run."
            ),
        )


class PreApprovedGate(ApprovalGate):
    """Approves only the exact permissions the operator listed for this run."""

    def __init__(self, permissions: Iterable[str]) -> None:
        self.permissions = frozenset(str(item).strip() for item in permissions if str(item).strip())

    def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        if request.permission in self.permissions:
            return ApprovalOutcome(
                granted=True,
                state=GRANTED,
                reason="Pre-approved by the operator for this run.",
            )
        return ApprovalOutcome(
            granted=False,
            state=DENIED,
            reason=(
                f"'{request.permission}' was not pre-approved for this run."
            ),
        )


class CallbackApprovalGate(ApprovalGate):
    """Delegates to a caller-supplied decision function."""

    def __init__(self, callback: Callable[[ApprovalRequest], bool]) -> None:
        self._callback = callback

    def request(self, request: ApprovalRequest) -> ApprovalOutcome:
        try:
            granted = bool(self._callback(request))
        except Exception:
            return ApprovalOutcome(
                granted=False,
                state=DENIED,
                reason="The approval prompt failed, so the action was refused.",
            )
        if granted:
            return ApprovalOutcome(granted=True, state=GRANTED, reason="Approved by the user.")
        return ApprovalOutcome(granted=False, state=DENIED, reason="Refused by the user.")
