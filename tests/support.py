"""Shared helpers for the Primee test suite (synthetic data only)."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from primee.connectors.registry import ConnectorRegistry
from primee.core.approval import ApprovalGate, ApprovalOutcome, DenyAllApprovalGate, GRANTED
from primee.core.audit import AuditLog, MemorySink
from primee.core.clock import FixedClock
from primee.core.config import load_mapping
from primee.core.runtime import PrimeeRuntime
from primee.core.skill_loader import discover_skills

from . import FIXTURES, SRC

BUNDLED_SKILLS = SRC / "primee" / "skills"
VALID_SKILLS = FIXTURES / "skills_valid"
BROKEN_SKILLS = FIXTURES / "skills_broken"
DATA = FIXTURES / "data"

FIXED_NOW = datetime(2026, 8, 27, 8, 0, 0, tzinfo=timezone(timedelta(hours=3, minutes=30)))
TODAY = "2026-08-27"


class AllowAllApprovalGate(ApprovalGate):
    """Grants every approval request. Used only to exercise the granted path."""

    def __init__(self) -> None:
        self.requests = []

    def request(self, request):
        self.requests.append(request)
        return ApprovalOutcome(granted=True, state=GRANTED, reason="Approved in a test.")


def fixed_clock() -> FixedClock:
    return FixedClock(FIXED_NOW)


def permissive_modes() -> dict:
    return {
        "vault.read": "auto",
        "vault.list": "auto",
        "vault.create": "auto",
        "vault.append": "auto",
        "vault.update": "approval",
        "connector.metrics.read": "auto",
        "connector.email.read": "auto",
        "connector.calendar.read": "auto",
        "connector.trends.read": "auto",
    }


def make_config(
    *,
    vault_root=None,
    modes=None,
    connectors=None,
    metrics_series=None,
    trends_sources=None,
    skills_dir=None,
    dry_run=False,
    audit_enabled=True,
):
    data = {
        "vault": {"root": str(vault_root) if vault_root else ""},
        "runtime": {
            "dry_run": dry_run,
            "skills_dir": str(skills_dir) if skills_dir else "",
        },
        "audit": {"enabled": audit_enabled},
        "permissions": {"default": "never", "modes": modes if modes is not None else permissive_modes()},
        "connectors": connectors or {},
    }
    if metrics_series is not None:
        data["metrics"] = {"series": metrics_series}
    if trends_sources is not None:
        data["trends"] = {"sources": trends_sources}
    return load_mapping(data)


def make_runtime(config, *, skills_root=None, approval_gate=None, dry_run=None):
    clock = fixed_clock()
    audit = AuditLog(MemorySink(), clock, enabled=config.audit.enabled)
    registry = discover_skills(Path(skills_root) if skills_root else BUNDLED_SKILLS)
    return PrimeeRuntime(
        config,
        registry=registry,
        approval_gate=approval_gate or DenyAllApprovalGate(),
        audit=audit,
        clock=clock,
        connectors=ConnectorRegistry.from_config(config),
        dry_run=dry_run,
    )


class TempVaultCase(unittest.TestCase):
    """Base class that provides a real, isolated, temporary Vault directory."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="primee-test-")
        self.tmp_path = Path(self._tmp.name)
        self.vault_root = self.tmp_path / "vault"
        self.vault_root.mkdir()
        self.outside = self.tmp_path / "outside"
        self.outside.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()
