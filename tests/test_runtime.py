"""End-to-end orchestration: routing, permissions, execution, Vault, audit."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode
from primee.core.runtime import CLARIFICATION_NEEDED, COMPLETED, FAILED

from .support import (
    AllowAllApprovalGate,
    DATA,
    TempVaultCase,
    VALID_SKILLS,
    make_config,
    make_runtime,
    TODAY,
)

METRICS_SERIES = [
    {"key": "website_visits", "label": "Website visits", "summaries": ["latest", "delta"]},
]


class RoutingIntegrationTests(TempVaultCase):
    def runtime(self, **kwargs):
        return make_runtime(make_config(vault_root=self.vault_root, **kwargs))

    def test_ambiguous_request_stops_before_any_skill_runs(self):
        runtime = make_runtime(make_config(), skills_root=VALID_SKILLS)
        outcome = runtime.handle("coffee")
        self.assertEqual(outcome.status, CLARIFICATION_NEEDED)
        self.assertEqual(outcome.error_code, ErrorCode.AMBIGUOUS_REQUEST)
        self.assertIsNone(outcome.result)
        self.assertEqual(
            sorted(c.skill_name for c in outcome.route.candidates), ["alpha", "beta"]
        )

    def test_unmatched_request_stops_before_any_skill_runs(self):
        outcome = self.runtime().handle("please defragment the toaster")
        self.assertEqual(outcome.status, CLARIFICATION_NEEDED)
        self.assertEqual(outcome.error_code, ErrorCode.NO_MATCHING_SKILL)

    def test_explicit_skill_argument_bypasses_routing(self):
        outcome = self.runtime(metrics_series=METRICS_SERIES).handle("", skill_name="metrics")
        self.assertEqual(outcome.route.method, "explicit")
        self.assertEqual(outcome.route.skill_name, "metrics")

    def test_explicit_unknown_skill_is_refused(self):
        outcome = self.runtime().handle("", skill_name="nope")
        self.assertEqual(outcome.error_code, ErrorCode.UNKNOWN_COMMAND)

    def test_every_request_is_audited(self):
        runtime = self.runtime()
        runtime.handle("please defragment the toaster")
        actions = [event.action for event in runtime.audit.events]
        self.assertIn("route", actions)


class PermissionEnforcementTests(TempVaultCase):
    def test_denied_connector_permission_stops_the_skill(self):
        config = make_config(
            vault_root=self.vault_root,
            modes={},  # everything falls back to "never"
            metrics_series=METRICS_SERIES,
            connectors={"metrics": {"provider": "mock", "fixture": str(DATA / "metrics.json")}},
        )
        runtime = make_runtime(config)
        outcome = runtime.handle("metrics report")
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(outcome.result.structured_data["series"], [])

    def test_vault_write_is_denied_when_the_policy_says_never(self):
        config = make_config(
            vault_root=self.vault_root,
            modes={"vault.read": "auto", "vault.list": "auto"},
        )
        runtime = make_runtime(config)
        outcome = runtime.handle(
            "daily plan",
            inputs={"candidates": [_candidate("Ship the landing page")]},
        )
        self.assertEqual(outcome.status, COMPLETED)
        write = outcome.vault_writes[0]
        self.assertFalse(write.performed)
        self.assertEqual(write.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertFalse((self.vault_root / "plans").exists())

    def test_write_needing_approval_is_refused_by_the_default_gate(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        (self.vault_root / "plans").mkdir()
        (self.vault_root / "plans" / f"{TODAY}.md").write_text("old plan", encoding="utf-8")
        outcome = runtime.handle(
            "daily plan",
            inputs={"candidates": [_candidate("Ship the landing page")], "overwrite": True},
        )
        write = outcome.vault_writes[0]
        self.assertEqual(write.operation, "update")
        self.assertFalse(write.performed)
        self.assertEqual(write.error_code, ErrorCode.APPROVAL_DENIED)
        self.assertEqual(
            (self.vault_root / "plans" / f"{TODAY}.md").read_text(encoding="utf-8"), "old plan"
        )

    def test_write_needing_approval_succeeds_once_approved(self):
        runtime = make_runtime(
            make_config(vault_root=self.vault_root), approval_gate=AllowAllApprovalGate()
        )
        (self.vault_root / "plans").mkdir()
        (self.vault_root / "plans" / f"{TODAY}.md").write_text("old plan", encoding="utf-8")
        outcome = runtime.handle(
            "daily plan",
            inputs={"candidates": [_candidate("Ship the landing page")], "overwrite": True},
        )
        self.assertTrue(outcome.vault_writes[0].performed)
        self.assertIn(
            "Ship the landing page",
            (self.vault_root / "plans" / f"{TODAY}.md").read_text(encoding="utf-8"),
        )

    def test_permission_decisions_are_audited_with_their_permission_name(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        permissions = {e.permission for e in runtime.audit.events if e.permission}
        self.assertIn("vault.create", permissions)


class DryRunTests(TempVaultCase):
    def test_dry_run_plans_the_write_but_creates_no_file(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root), dry_run=True)
        outcome = runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(outcome.dry_run)
        write = outcome.vault_writes[0]
        self.assertFalse(write.performed)
        self.assertEqual(write.state, "dry_run")
        self.assertEqual(write.error_code, ErrorCode.DRY_RUN_BLOCKED)
        self.assertFalse((self.vault_root / "plans").exists())

    def test_dry_run_is_recorded_in_the_audit_trail(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root), dry_run=True)
        runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        states = {event.approval_state for event in runtime.audit.events}
        self.assertIn("dry_run", states)

    def test_dry_run_from_configuration_is_honoured(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root, dry_run=True))
        outcome = runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        self.assertTrue(outcome.dry_run)
        self.assertFalse((self.vault_root / "plans").exists())

    def test_the_vault_skill_itself_refuses_writes_during_a_dry_run(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root), dry_run=True)
        outcome = runtime.handle(
            "", skill_name="vault", inputs={"operation": "create", "path": "a.md", "content": "x"}
        )
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.DRY_RUN_BLOCKED)
        self.assertFalse((self.vault_root / "a.md").exists())


class VaultGatewayTests(TempVaultCase):
    def test_a_skill_write_is_performed_by_the_vault_skill(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        outcome = runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        self.assertTrue(outcome.vault_writes[0].performed)
        self.assertEqual(outcome.vault_writes[0].requested_by, "plan")
        events = [e for e in runtime.audit.events if e.action.startswith("vault.create")]
        # Two events per write: the permission decision, then the write itself.
        self.assertEqual([e.outcome for e in events], ["allowed", "written"])
        self.assertEqual(events[-1].detail["performed_by"], "vault")

    def test_audit_records_the_requesting_skill_but_not_the_content(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        secret_marker = "TOP-LEVEL-PLAN-BODY-MARKER"
        runtime.handle(
            "daily plan",
            inputs={"candidates": [_candidate(secret_marker)]},
        )
        serialized = str([event.to_dict() for event in runtime.audit.events])
        self.assertIn("plan", serialized)
        self.assertNotIn(secret_marker, serialized)

    def test_a_skill_without_vault_read_cannot_read(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        skill = runtime.registry.require("metrics")
        context = runtime._build_context(
            "req", skill.name, skill.manifest.required_permissions, "", {}
        )
        read = context.vault.read("plans/anything.md")
        self.assertFalse(read.ok)
        self.assertEqual(read.error_code, ErrorCode.PERMISSION_NOT_DECLARED)

    def test_an_unconfigured_vault_fails_the_write_without_crashing(self):
        runtime = make_runtime(make_config())
        outcome = runtime.handle("daily plan", inputs={"candidates": [_candidate("A task")]})
        self.assertEqual(outcome.status, COMPLETED)
        self.assertFalse(outcome.vault_writes[0].performed)
        self.assertEqual(outcome.vault_writes[0].error_code, ErrorCode.VAULT_NOT_CONFIGURED)


class HandlerFailureTests(TempVaultCase):
    def test_a_crashing_handler_produces_a_sanitized_failure(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        skill = runtime.registry.require("metrics")

        def explode(_context):
            raise RuntimeError("password=PLACEHOLDERVALUE leaked in the traceback")

        skill._handler = explode
        outcome = runtime.handle("metrics report")
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.HANDLER_FAILED)
        self.assertNotIn("PLACEHOLDERVALUE", str(outcome.to_dict()))

    def test_a_handler_returning_the_wrong_type_is_rejected(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root))
        runtime.registry.require("metrics")._handler = lambda _context: {"success": True}
        outcome = runtime.handle("metrics report")
        self.assertEqual(outcome.error_code, ErrorCode.RESULT_INVALID)

    def test_an_invalid_result_is_rejected(self):
        from primee.core.result_types import SkillResult

        runtime = make_runtime(make_config(vault_root=self.vault_root))
        runtime.registry.require("metrics")._handler = lambda _c: SkillResult(
            success=True, skill_name="metrics", summary=""
        )
        outcome = runtime.handle("metrics report")
        self.assertEqual(outcome.error_code, ErrorCode.RESULT_INVALID)


class ExternalActionTests(TempVaultCase):
    def test_external_actions_are_never_executed_only_recorded(self):
        from primee.core.result_types import ExternalAction, SkillResult

        runtime = make_runtime(make_config(vault_root=self.vault_root))
        runtime.registry.require("inbox")._handler = lambda _c: SkillResult.ok(
            "inbox",
            "one item",
            proposed_external_actions=[
                ExternalAction(
                    action="send_email",
                    target="sample@example.invalid",
                    description="reply to the quote request",
                    required_permission="email.send",
                )
            ],
        )
        outcome = runtime.handle("morning brief")
        self.assertEqual(len(outcome.pending_external_actions), 1)
        pending = outcome.pending_external_actions[0]
        self.assertEqual(pending["state"], "pending_user_approval")
        self.assertNotIn("sample@example.invalid", str(pending))
        events = [e for e in runtime.audit.events if e.action == "external:send_email"]
        self.assertEqual(events[0].outcome, "not_executed")


def _candidate(title: str) -> dict:
    return {
        "title": title,
        "reason": "It is the nearest deadline.",
        "expected_outcome": "The page is live.",
        "completion_condition": "The URL returns the new content.",
    }


if __name__ == "__main__":
    unittest.main()
