"""Append-only, sanitized audit trail.

Every routing decision, permission decision, approval decision, skill execution
and Vault write produces one audit event.  Events record *what happened*, never
the content of a note, an email body or a calendar entry.

The audit log lives outside the git repository and outside the Vault.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .clock import Clock, timestamp_iso
from .redaction import redact_structure, redact_text

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    request_id: str
    actor_skill: str
    action: str
    approval_state: str
    outcome: str
    permission: Optional[str] = None
    error_code: Optional[str] = None
    detail: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return asdict(self)


class AuditSink:
    def write(self, event: AuditEvent) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class MemorySink(AuditSink):
    """In-memory sink used by tests and by dry runs."""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def write(self, event: AuditEvent) -> None:
        self.events.append(event)


class JsonlSink(AuditSink):
    """Append-only JSON Lines file, one event per line."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def write(self, event: AuditEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True)
        descriptor = os.open(
            self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
        )
        try:
            with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:  # pragma: no cover - defensive
            os.close(descriptor)
            raise


class NullSink(AuditSink):
    def write(self, event: AuditEvent) -> None:
        return None


class AuditLog:
    """Builds sanitized events and hands them to a sink."""

    def __init__(self, sink: AuditSink, clock: Clock, *, enabled: bool = True) -> None:
        self._sink = sink
        self._clock = clock
        self.enabled = enabled
        self.events: list[AuditEvent] = []

    def new_request_id(self) -> str:
        return uuid.uuid4().hex[:16]

    def record(
        self,
        *,
        request_id: str,
        actor_skill: str,
        action: str,
        outcome: str,
        approval_state: str = "not_requested",
        permission: Optional[str] = None,
        error_code: Optional[str] = None,
        detail: Optional[dict[str, Any]] = None,
    ) -> AuditEvent:
        event = AuditEvent(
            timestamp=timestamp_iso(self._clock),
            request_id=request_id,
            actor_skill=redact_text(str(actor_skill)),
            action=str(action),
            approval_state=str(approval_state),
            outcome=str(outcome),
            permission=permission,
            error_code=error_code,
            detail=redact_structure(detail or {}),
        )
        self.events.append(event)
        if self.enabled:
            self._sink.write(event)
        return event
