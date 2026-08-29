"""Deny-by-default permissions, declaration checks and approval gating."""

from __future__ import annotations

import unittest

from primee.core.approval import (
    ApprovalRequest,
    CallbackApprovalGate,
    DenyAllApprovalGate,
    PreApprovedGate,
)
from primee.core.errors import ErrorCode
from primee.core.permissions import (
    APPROVAL,
    AUTO,
    NEVER,
    PERMISSIONS,
    PermissionEngine,
    PermissionPolicy,
)


class PolicyTests(unittest.TestCase):
    def test_default_is_deny(self):
        policy = PermissionPolicy.from_mapping({})
        self.assertEqual(policy.default_mode, NEVER)
        for name in PERMISSIONS:
            self.assertEqual(policy.mode_for(name), NEVER)

    def test_unknown_permission_names_in_policy_are_ignored(self):
        policy = PermissionPolicy.from_mapping({"modes": {"vault.nuke": "auto"}})
        self.assertNotIn("vault.nuke", policy.modes)

    def test_invalid_mode_names_are_ignored(self):
        policy = PermissionPolicy.from_mapping({"modes": {"vault.read": "sure_why_not"}})
        self.assertEqual(policy.mode_for("vault.read"), NEVER)

    def test_invalid_default_falls_back_to_never(self):
        policy = PermissionPolicy.from_mapping({"default": "allow_everything"})
        self.assertEqual(policy.default_mode, NEVER)


class EngineTests(unittest.TestCase):
    def engine(self, **modes):
        return PermissionEngine(PermissionPolicy.from_mapping({"modes": modes}))

    def test_undeclared_permission_is_denied_even_when_policy_allows(self):
        decision = self.engine(**{"vault.create": AUTO}).evaluate(frozenset(), "vault.create")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error_code, ErrorCode.PERMISSION_NOT_DECLARED)

    def test_declared_but_unmapped_permission_is_denied(self):
        decision = self.engine().evaluate(frozenset({"vault.create"}), "vault.create")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error_code, ErrorCode.PERMISSION_DENIED)

    def test_auto_permission_is_allowed_without_approval(self):
        decision = self.engine(**{"vault.read": AUTO}).evaluate(frozenset({"vault.read"}), "vault.read")
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.requires_approval)

    def test_approval_permission_is_allowed_but_needs_approval(self):
        decision = self.engine(**{"vault.update": APPROVAL}).evaluate(
            frozenset({"vault.update"}), "vault.update"
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_unknown_permission_is_denied(self):
        decision = self.engine().evaluate(frozenset({"vault.read"}), "vault.teleport")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.error_code, ErrorCode.PERMISSION_UNKNOWN)

    def test_reserved_capabilities_are_denied_even_if_policy_allows(self):
        for reserved in ("email.send", "fs.write", "system.execute", "audio.capture"):
            engine = self.engine(**{reserved: AUTO})
            decision = engine.evaluate(frozenset({reserved}), reserved)
            self.assertFalse(decision.allowed, msg=reserved)
            self.assertIn("later Primee step", decision.reason)


class ApprovalGateTests(unittest.TestCase):
    def request(self, permission="vault.update"):
        return ApprovalRequest(
            skill_name="plan", permission=permission, action="write", description="test"
        )

    def test_deny_all_gate_refuses(self):
        outcome = DenyAllApprovalGate().request(self.request())
        self.assertFalse(outcome.granted)

    def test_pre_approved_gate_only_grants_the_named_permission(self):
        gate = PreApprovedGate(["vault.update"])
        self.assertTrue(gate.request(self.request("vault.update")).granted)
        self.assertFalse(gate.request(self.request("vault.create")).granted)

    def test_callback_gate_failure_is_treated_as_refusal(self):
        def explode(_request):
            raise RuntimeError("prompt crashed")

        outcome = CallbackApprovalGate(explode).request(self.request())
        self.assertFalse(outcome.granted)
        self.assertNotIn("prompt crashed", outcome.reason)


if __name__ == "__main__":
    unittest.main()
