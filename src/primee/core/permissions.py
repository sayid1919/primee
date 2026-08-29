"""Deny-by-default permission engine.

Two independent gates must both agree before an action is allowed:

1. **Declaration** - the skill's own ``SKILL.md`` must list the permission in
   ``required_permissions``.  A skill can never use a capability it did not
   declare, even if the policy would allow it.
2. **Policy** - the user's permission policy must map that permission to
   ``auto`` or ``approval``.  Anything not explicitly mapped falls back to the
   default mode, which is ``never``.

Skills can never widen their own permissions: the manifest is read once at load
time and the policy comes from user-owned configuration outside the skill.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from .errors import ErrorCode

AUTO = "auto"
APPROVAL = "approval"
NEVER = "never"
MODES = frozenset({AUTO, APPROVAL, NEVER})

PERMISSION_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")

#: Every permission Primee understands, with a short human description.
#: Permissions marked ``implemented=False`` are reserved for later steps: they
#: can be declared and reasoned about, but no code path can act on them yet.
@dataclass(frozen=True)
class PermissionSpec:
    name: str
    description: str
    implemented: bool
    skill_requestable: bool = True


_SPECS: tuple[PermissionSpec, ...] = (
    PermissionSpec("vault.read", "Read a file from the configured Vault.", True),
    PermissionSpec("vault.list", "List files inside the configured Vault.", True),
    PermissionSpec("vault.create", "Create a new file in the Vault.", True),
    PermissionSpec("vault.append", "Append to an existing Vault file.", True),
    PermissionSpec("vault.update", "Replace the content of an existing Vault file.", True),
    PermissionSpec("connector.metrics.read", "Read numbers from a metrics connector.", True),
    PermissionSpec("connector.email.read", "Read message headers from an email connector.", True),
    PermissionSpec("connector.calendar.read", "Read events from a calendar connector.", True),
    PermissionSpec("connector.trends.read", "Read a snapshot from a trends connector.", True),
    # Reserved for later steps. Declaring them is allowed; acting on them is not.
    PermissionSpec("fs.read", "Read files outside the Vault from an allowlisted folder.", False),
    PermissionSpec("fs.write", "Write files outside the Vault.", False),
    PermissionSpec("email.send", "Send or reply to an email.", False),
    PermissionSpec("calendar.write", "Create, change or delete a calendar event.", False),
    PermissionSpec("network.fetch", "Fetch a remote resource over the network.", False),
    PermissionSpec("system.execute", "Run an operating system command.", False),
    PermissionSpec("audio.capture", "Capture microphone audio.", False),
    PermissionSpec("camera.capture", "Capture camera video.", False),
    PermissionSpec("audit.write", "Append to the audit log.", True, skill_requestable=False),
)

PERMISSIONS: dict[str, PermissionSpec] = {spec.name: spec for spec in _SPECS}
SKILL_REQUESTABLE = frozenset(
    name for name, spec in PERMISSIONS.items() if spec.skill_requestable
)
IMPLEMENTED = frozenset(name for name, spec in PERMISSIONS.items() if spec.implemented)


@dataclass(frozen=True)
class Decision:
    """The outcome of evaluating one permission for one skill."""

    permission: str
    mode: str
    allowed: bool
    requires_approval: bool
    reason: str
    error_code: Optional[str] = None

    @property
    def denied(self) -> bool:
        return not self.allowed


@dataclass(frozen=True)
class PermissionPolicy:
    """User-owned mapping from permission name to mode."""

    modes: Mapping[str, str] = field(default_factory=dict)
    default_mode: str = NEVER

    @classmethod
    def from_mapping(cls, raw: Optional[Mapping[str, object]]) -> "PermissionPolicy":
        raw = raw or {}
        default = str(raw.get("default", NEVER)).strip().lower()
        if default not in MODES:
            default = NEVER
        modes_raw = raw.get("modes", {})
        modes: dict[str, str] = {}
        if isinstance(modes_raw, Mapping):
            for key, value in modes_raw.items():
                name = str(key).strip()
                mode = str(value).strip().lower()
                if name in PERMISSIONS and mode in MODES:
                    modes[name] = mode
        return cls(modes=modes, default_mode=default)

    def mode_for(self, permission: str) -> str:
        return self.modes.get(permission, self.default_mode)

    def describe(self) -> dict:
        return {
            "default": self.default_mode,
            "modes": {name: self.mode_for(name) for name in sorted(PERMISSIONS)},
        }


class PermissionEngine:
    """Evaluates permissions for skills.  Holds no state beyond the policy."""

    def __init__(self, policy: PermissionPolicy) -> None:
        self.policy = policy

    def evaluate(self, declared: frozenset[str], permission: str) -> Decision:
        if permission not in PERMISSIONS:
            return Decision(
                permission=permission,
                mode=NEVER,
                allowed=False,
                requires_approval=False,
                reason="Unknown permission.",
                error_code=ErrorCode.PERMISSION_UNKNOWN,
            )
        if permission not in declared:
            return Decision(
                permission=permission,
                mode=NEVER,
                allowed=False,
                requires_approval=False,
                reason="The skill did not declare this permission in SKILL.md.",
                error_code=ErrorCode.PERMISSION_NOT_DECLARED,
            )
        spec = PERMISSIONS[permission]
        if not spec.implemented:
            return Decision(
                permission=permission,
                mode=NEVER,
                allowed=False,
                requires_approval=False,
                reason="This capability is reserved for a later Primee step and is not implemented.",
                error_code=ErrorCode.PERMISSION_DENIED,
            )
        mode = self.policy.mode_for(permission)
        if mode == AUTO:
            return Decision(
                permission=permission,
                mode=AUTO,
                allowed=True,
                requires_approval=False,
                reason="Allowed automatically by the permission policy.",
            )
        if mode == APPROVAL:
            return Decision(
                permission=permission,
                mode=APPROVAL,
                allowed=True,
                requires_approval=True,
                reason="The permission policy requires explicit approval.",
            )
        return Decision(
            permission=permission,
            mode=NEVER,
            allowed=False,
            requires_approval=False,
            reason="The permission policy denies this capability.",
            error_code=ErrorCode.PERMISSION_DENIED,
        )

    def evaluate_all(self, declared: frozenset[str], permissions: list[str]) -> list[Decision]:
        return [self.evaluate(declared, permission) for permission in permissions]
