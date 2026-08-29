"""Primee Core orchestration.

One request flows through exactly this sequence:

    route -> declare -> permit -> approve -> execute -> validate
          -> persist through the Vault skill -> audit

No step can be skipped and no skill can reach around it: handlers receive only
data plus a Vault *read* proxy, and every persistent write is a proposal that
Primee Core hands to the Vault skill after checking permissions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..connectors.base import DeniedConnector
from ..connectors.registry import ConnectorRegistry
from .approval import ApprovalGate, ApprovalRequest, DenyAllApprovalGate, DRY_RUN, NOT_REQUESTED
from .audit import AuditLog, MemorySink
from .clock import Clock, SystemClock
from .config import PrimeeConfig
from .context import DeniedVaultAccess, SkillContext, VaultAccess, VaultReadResult
from .errors import ErrorCode, PrimeeError
from .permissions import PermissionEngine, PermissionPolicy
from .redaction import redact_text
from .result_types import SkillResult, VaultWrite
from .router import DeterministicRouter, RouteDecision, Router
from .skill_loader import SkillRegistry, discover_skills

VAULT_SKILL = "vault"

COMPLETED = "completed"
CLARIFICATION_NEEDED = "clarification_needed"
DENIED = "denied"
FAILED = "failed"


def bundled_skills_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "skills"


@dataclass
class VaultWriteOutcome:
    path: str
    operation: str
    requested_by: str
    performed: bool
    state: str
    reason: str
    error_code: Optional[str] = None
    bytes_written: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "operation": self.operation,
            "requested_by": self.requested_by,
            "performed": self.performed,
            "state": self.state,
            "reason": self.reason,
            "error_code": self.error_code,
            "bytes_written": self.bytes_written,
        }


@dataclass
class RuntimeOutcome:
    status: str
    request_id: str
    route: RouteDecision
    result: Optional[SkillResult] = None
    vault_writes: list[VaultWriteOutcome] = field(default_factory=list)
    pending_external_actions: list[dict] = field(default_factory=list)
    message: str = ""
    error_code: Optional[str] = None
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return self.status == COMPLETED and bool(self.result and self.result.success)

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "request_id": self.request_id,
            "dry_run": self.dry_run,
            "message": self.message,
            "error_code": self.error_code,
            "route": self.route.to_dict(),
            "result": self.result.to_dict() if self.result else None,
            "vault_writes": [item.to_dict() for item in self.vault_writes],
            "pending_external_actions": list(self.pending_external_actions),
        }


class GuardedConnectors:
    """A permission-checked view of the connector registry.

    A skill never touches :class:`ConnectorRegistry` directly.  Every lookup runs
    through the permission engine and the approval gate first; a refusal yields a
    :class:`DeniedConnector` that returns no data at all.
    """

    _KINDS = ("metrics", "email", "calendar", "trends")

    def __init__(self, runtime: "PrimeeRuntime", request_id: str, skill_name: str, declared: frozenset[str]) -> None:
        self._runtime = runtime
        self._request_id = request_id
        self._skill_name = skill_name
        self._declared = declared
        self._cache: dict[str, Any] = {}

    def get(self, kind: str):
        if kind in self._cache:
            return self._cache[kind]
        permission = f"connector.{kind}.read"
        allowed, _state, _code, reason = self._runtime._permit(
            self._request_id, self._skill_name, self._declared, permission, permission
        )
        connector = (
            self._runtime.connectors.get(kind) if allowed else DeniedConnector(kind, reason)
        )
        self._cache[kind] = connector
        return connector

    def metrics(self):
        return self.get("metrics")

    def email(self):
        return self.get("email")

    def calendar(self):
        return self.get("calendar")

    def trends(self):
        return self.get("trends")

    def describe(self) -> dict:
        return {kind: self.get(kind).describe() for kind in self._KINDS}


class PrimeeRuntime:
    """The entry point every Primee front end uses."""

    def __init__(
        self,
        config: PrimeeConfig,
        *,
        registry: Optional[SkillRegistry] = None,
        router: Optional[Router] = None,
        approval_gate: Optional[ApprovalGate] = None,
        audit: Optional[AuditLog] = None,
        clock: Optional[Clock] = None,
        connectors: Optional[ConnectorRegistry] = None,
        storage: Any = None,
        dry_run: Optional[bool] = None,
    ) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.dry_run = config.runtime.dry_run if dry_run is None else bool(dry_run)
        self.registry = registry if registry is not None else discover_skills(self.skills_root())
        self.router = router or DeterministicRouter(
            min_score=config.runtime.min_route_score,
            ambiguity_margin=config.runtime.ambiguity_margin,
        )
        self.permissions = PermissionEngine(config.permissions or PermissionPolicy())
        self.approval = approval_gate or DenyAllApprovalGate()
        self.audit = audit or AuditLog(MemorySink(), self.clock, enabled=config.audit.enabled)
        self.connectors = connectors or ConnectorRegistry.from_config(config)
        self._storage = storage
        self._storage_error: Optional[PrimeeError] = None

    # -- setup ----------------------------------------------------------
    def skills_root(self) -> Path:
        configured = self.config.runtime.skills_dir
        if configured:
            from .config import expand

            return Path(expand(configured))
        return bundled_skills_dir()

    def storage(self):
        """Lazily build the Vault storage so an unconfigured Vault is not fatal."""
        if self._storage is not None:
            return self._storage
        if self._storage_error is not None:
            raise self._storage_error
        from ..skills.vault.storage import VaultStorage

        try:
            self._storage = VaultStorage(
                self.config.vault.root, max_file_bytes=self.config.vault.max_file_bytes
            )
        except PrimeeError as exc:
            self._storage_error = exc
            raise
        return self._storage

    # -- public API -----------------------------------------------------
    def handle(
        self,
        request: str,
        *,
        inputs: Optional[dict[str, Any]] = None,
        skill_name: Optional[str] = None,
    ) -> RuntimeOutcome:
        request_id = self.audit.new_request_id()
        inputs = dict(inputs or {})

        if skill_name:
            decision = self._route_explicit(skill_name, request)
        else:
            decision = self.router.route(request, self.registry)

        self.audit.record(
            request_id=request_id,
            actor_skill="core.router",
            action="route",
            outcome=decision.status,
            error_code=decision.error_code,
            detail={
                "skill_name": decision.skill_name,
                "candidates": [c.skill_name for c in decision.candidates],
                "method": decision.method,
            },
        )

        if not decision.matched:
            return RuntimeOutcome(
                status=CLARIFICATION_NEEDED,
                request_id=request_id,
                route=decision,
                message=decision.explanation,
                error_code=decision.error_code,
                dry_run=self.dry_run,
            )

        return self._execute(request_id, decision, request, inputs)

    def explain(self, request: str) -> RouteDecision:
        """Route without executing anything."""
        return self.router.route(request, self.registry)

    # -- internals ------------------------------------------------------
    def _route_explicit(self, skill_name: str, request: str) -> RouteDecision:
        from .router import MATCHED, UNKNOWN_COMMAND
        from .router import SkillScore

        name = str(skill_name).strip().lower()
        if self.registry.get(name) is None:
            return RouteDecision(
                status=UNKNOWN_COMMAND,
                request=request,
                explanation=(
                    f"There is no installed skill named '{name}'. "
                    f"Installed skills: {', '.join(self.registry.names()) or 'none'}."
                ),
                error_code=ErrorCode.UNKNOWN_COMMAND,
                method="explicit",
            )
        return RouteDecision(
            status=MATCHED,
            request=request,
            skill_name=name,
            candidates=[SkillScore(skill_name=name, score=1.0)],
            all_scores=[SkillScore(skill_name=name, score=1.0)],
            explanation=f"The caller selected skill '{name}' explicitly.",
            method="explicit",
        )

    def _permit(
        self, request_id: str, skill_name: str, declared: frozenset[str], permission: str, action: str
    ) -> tuple[bool, str, Optional[str], str]:
        """Return ``(allowed, approval_state, error_code, reason)``."""
        decision = self.permissions.evaluate(declared, permission)
        if not decision.allowed:
            self.audit.record(
                request_id=request_id,
                actor_skill=skill_name,
                action=action,
                outcome="denied",
                permission=permission,
                error_code=decision.error_code,
                detail={"mode": decision.mode, "reason": decision.reason},
            )
            return False, NOT_REQUESTED, decision.error_code, decision.reason

        if not decision.requires_approval:
            self.audit.record(
                request_id=request_id,
                actor_skill=skill_name,
                action=action,
                outcome="allowed",
                permission=permission,
                detail={"mode": decision.mode},
            )
            return True, NOT_REQUESTED, None, decision.reason

        outcome = self.approval.request(
            ApprovalRequest(
                skill_name=skill_name,
                permission=permission,
                action=action,
                description=f"{skill_name} requests '{permission}' for {action}.",
            )
        )
        self.audit.record(
            request_id=request_id,
            actor_skill=skill_name,
            action=action,
            outcome="allowed" if outcome.granted else "denied",
            approval_state=outcome.state,
            permission=permission,
            error_code=None if outcome.granted else ErrorCode.APPROVAL_DENIED,
            detail={"mode": decision.mode, "reason": outcome.reason},
        )
        if outcome.granted:
            return True, outcome.state, None, outcome.reason
        return False, outcome.state, ErrorCode.APPROVAL_DENIED, outcome.reason

    def _build_context(
        self, request_id: str, skill_name: str, declared: frozenset[str], request: str, inputs: dict
    ) -> SkillContext:
        if "vault.read" in declared:
            vault = VaultAccess(
                _reader=lambda path: self._vault_read(request_id, skill_name, declared, path),
                _lister=lambda prefix: self._vault_list(request_id, skill_name, declared, prefix),
            )
        else:
            vault = DeniedVaultAccess()
        storage = None
        if skill_name == VAULT_SKILL:
            try:
                storage = self.storage()
            except PrimeeError:
                storage = None
        return SkillContext(
            skill_name=skill_name,
            request=request,
            inputs=inputs,
            config=self.config,
            clock=self.clock,
            connectors=GuardedConnectors(self, request_id, skill_name, declared),
            vault=vault,
            dry_run=self.dry_run,
            storage=storage,
        )

    def _execute(
        self, request_id: str, decision: RouteDecision, request: str, inputs: dict
    ) -> RuntimeOutcome:
        skill = self.registry.require(decision.skill_name)
        declared = skill.manifest.required_permissions

        try:
            handler = skill.load_handler()
        except PrimeeError as exc:
            self.audit.record(
                request_id=request_id,
                actor_skill=skill.name,
                action="load_handler",
                outcome="error",
                error_code=exc.code,
                detail=exc.sanitized_detail(),
            )
            return RuntimeOutcome(
                status=FAILED,
                request_id=request_id,
                route=decision,
                message=exc.sanitized_message(),
                error_code=exc.code,
                dry_run=self.dry_run,
            )

        if skill.name == VAULT_SKILL:
            denial = self._gate_direct_vault_call(request_id, declared, inputs)
            if denial is not None:
                return RuntimeOutcome(
                    status=DENIED,
                    request_id=request_id,
                    route=decision,
                    result=denial,
                    message=denial.sanitized_error_message or "",
                    error_code=denial.error_code,
                    dry_run=self.dry_run,
                )

        context = self._build_context(request_id, skill.name, declared, request, inputs)

        try:
            result = handler(context)
        except PrimeeError as exc:
            result = SkillResult.fail(
                skill.name, f"The '{skill.name}' skill could not finish.", exc.code, exc.message
            )
        except Exception as exc:  # noqa: BLE001 - handlers are untrusted code
            self.audit.record(
                request_id=request_id,
                actor_skill=skill.name,
                action="execute",
                outcome="error",
                error_code=ErrorCode.HANDLER_FAILED,
                detail={"exception_type": type(exc).__name__},
            )
            result = SkillResult.fail(
                skill.name,
                f"The '{skill.name}' skill raised an unexpected error.",
                ErrorCode.HANDLER_FAILED,
                "The skill handler raised an unexpected error. "
                "The details were withheld to avoid leaking sensitive data.",
            )

        if not isinstance(result, SkillResult):
            result = SkillResult.fail(
                skill.name,
                f"The '{skill.name}' skill returned an invalid result.",
                ErrorCode.RESULT_INVALID,
                "A skill handler must return a SkillResult instance.",
            )

        try:
            result.validate()
        except PrimeeError as exc:
            self.audit.record(
                request_id=request_id,
                actor_skill=skill.name,
                action="validate_result",
                outcome="rejected",
                error_code=exc.code,
                detail={"reason": exc.sanitized_message()},
            )
            result = SkillResult.fail(
                skill.name,
                f"The '{skill.name}' skill returned an invalid result.",
                ErrorCode.RESULT_INVALID,
                exc.message,
            ).validate()

        self.audit.record(
            request_id=request_id,
            actor_skill=skill.name,
            action="execute",
            outcome="success" if result.success else "failure",
            error_code=result.error_code,
            detail={
                "warnings": len(result.warnings),
                "proposed_vault_writes": len(result.proposed_vault_writes),
                "proposed_external_actions": len(result.proposed_external_actions),
            },
        )

        vault_outcomes: list[VaultWriteOutcome] = []
        if result.success and skill.name != VAULT_SKILL:
            for write in result.proposed_vault_writes:
                vault_outcomes.append(
                    self._apply_vault_write(request_id, skill.name, declared, write)
                )

        pending = []
        for action in result.proposed_external_actions:
            described = action.describe()
            described["state"] = "pending_user_approval"
            described["note"] = (
                "Primee Step One never performs external actions. This was recorded "
                "as a proposal only."
            )
            pending.append(described)
            self.audit.record(
                request_id=request_id,
                actor_skill=skill.name,
                action=f"external:{action.action}",
                outcome="not_executed",
                approval_state="pending",
                permission=action.required_permission,
                detail={"target": described["target"]},
            )

        status = COMPLETED if result.success else FAILED
        message = result.summary if result.success else (result.sanitized_error_message or "")
        return RuntimeOutcome(
            status=status,
            request_id=request_id,
            route=decision,
            result=result,
            vault_writes=vault_outcomes,
            pending_external_actions=pending,
            message=message,
            error_code=result.error_code,
            dry_run=self.dry_run,
        )

    def _gate_direct_vault_call(
        self, request_id: str, declared: frozenset[str], inputs: dict
    ) -> Optional[SkillResult]:
        """Permission-check a Vault operation that a caller requested directly.

        Without this, ``primee run --skill vault`` would reach storage without
        passing through the permission policy, which the deny-by-default rule
        forbids.
        """
        operation = str(inputs.get("operation", "")).strip().lower()
        if operation not in ("read", "list", "create", "append", "update"):
            return None  # the handler reports the invalid operation itself
        permission = f"vault.{operation}"
        allowed, _state, error_code, reason = self._permit(
            request_id, VAULT_SKILL, declared, permission, f"{permission}:direct"
        )
        if allowed:
            return None
        return SkillResult.fail(
            VAULT_SKILL,
            f"The Vault refused a direct '{operation}' request.",
            error_code or ErrorCode.PERMISSION_DENIED,
            reason,
        ).validate()

    # -- vault plumbing --------------------------------------------------
    def _call_vault_skill(self, request_id: str, requested_by: str, payload: dict) -> SkillResult:
        vault_skill = self.registry.get(VAULT_SKILL)
        if vault_skill is None:
            return SkillResult.fail(
                VAULT_SKILL,
                "The Vault skill is not installed.",
                ErrorCode.SKILL_NOT_FOUND,
                "Primee cannot persist anything without the 'vault' skill.",
            ).validate()
        try:
            handler = vault_skill.load_handler()
            storage = self.storage()
        except PrimeeError as exc:
            return SkillResult.fail(
                VAULT_SKILL, "The Vault is not available.", exc.code, exc.message
            ).validate()

        context = SkillContext(
            skill_name=VAULT_SKILL,
            request=f"{payload.get('operation', '?')} {payload.get('path', '')}",
            inputs={**payload, "requested_by": requested_by},
            config=self.config,
            clock=self.clock,
            connectors=self.connectors,
            vault=DeniedVaultAccess(),
            dry_run=self.dry_run,
            storage=storage,
        )
        try:
            result = handler(context)
            if not isinstance(result, SkillResult):
                raise PrimeeError(
                    ErrorCode.RESULT_INVALID, "The Vault skill returned an invalid result."
                )
            return result.validate()
        except PrimeeError as exc:
            return SkillResult.fail(
                VAULT_SKILL, "The Vault operation failed.", exc.code, exc.message
            ).validate()
        except Exception:  # noqa: BLE001
            return SkillResult.fail(
                VAULT_SKILL,
                "The Vault operation failed.",
                ErrorCode.VAULT_IO_ERROR,
                "The Vault raised an unexpected error; details were withheld.",
            ).validate()

    def _vault_read(
        self, request_id: str, skill_name: str, declared: frozenset[str], path: str
    ) -> VaultReadResult:
        allowed, _state, error_code, reason = self._permit(
            request_id, skill_name, declared, "vault.read", f"vault.read:{path}"
        )
        if not allowed:
            return VaultReadResult(ok=False, found=False, error_code=error_code, message=reason)
        result = self._call_vault_skill(
            request_id, skill_name, {"operation": "read", "path": path}
        )
        if not result.success:
            found = result.error_code != ErrorCode.VAULT_FILE_MISSING
            return VaultReadResult(
                ok=False,
                found=False if not found else False,
                error_code=result.error_code,
                message=result.sanitized_error_message or "",
            )
        return VaultReadResult(
            ok=True, found=True, content=result.structured_data.get("content", "")
        )

    def _vault_list(
        self, request_id: str, skill_name: str, declared: frozenset[str], prefix: str
    ) -> list[str]:
        allowed, _state, _code, _reason = self._permit(
            request_id, skill_name, declared, "vault.list", f"vault.list:{prefix or '/'}"
        )
        if not allowed:
            return []
        result = self._call_vault_skill(
            request_id, skill_name, {"operation": "list", "prefix": prefix}
        )
        if not result.success:
            return []
        return list(result.structured_data.get("paths", []))

    def _apply_vault_write(
        self, request_id: str, skill_name: str, declared: frozenset[str], write: VaultWrite
    ) -> VaultWriteOutcome:
        action = f"vault.{write.operation}:{write.path}"
        allowed, state, error_code, reason = self._permit(
            request_id, skill_name, declared, write.permission, action
        )
        if not allowed:
            return VaultWriteOutcome(
                path=write.path,
                operation=write.operation,
                requested_by=skill_name,
                performed=False,
                state=state,
                reason=reason,
                error_code=error_code,
            )

        if self.dry_run:
            self.audit.record(
                request_id=request_id,
                actor_skill=skill_name,
                action=action,
                outcome="skipped",
                approval_state=DRY_RUN,
                permission=write.permission,
                error_code=ErrorCode.DRY_RUN_BLOCKED,
                detail=write.describe(),
            )
            return VaultWriteOutcome(
                path=write.path,
                operation=write.operation,
                requested_by=skill_name,
                performed=False,
                state=DRY_RUN,
                reason="Dry run: the Vault write was planned but not performed.",
                error_code=ErrorCode.DRY_RUN_BLOCKED,
            )

        result = self._call_vault_skill(
            request_id,
            skill_name,
            {"operation": write.operation, "path": write.path, "content": write.content},
        )
        performed = bool(result.success)
        self.audit.record(
            request_id=request_id,
            actor_skill=skill_name,
            action=action,
            outcome="written" if performed else "failed",
            approval_state=state,
            permission=write.permission,
            error_code=result.error_code,
            detail={
                **write.describe(),
                "performed_by": VAULT_SKILL,
                "message": result.sanitized_error_message or "",
            },
        )
        return VaultWriteOutcome(
            path=write.path,
            operation=write.operation,
            requested_by=skill_name,
            performed=performed,
            state=state if performed else "failed",
            reason=result.summary if performed else (result.sanitized_error_message or ""),
            error_code=result.error_code,
            bytes_written=int(result.structured_data.get("bytes_written", 0) or 0),
        )
