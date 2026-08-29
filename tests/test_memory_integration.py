"""Skills reaching memory through the Vault skill, under the permission layer."""

from __future__ import annotations

import unittest

from primee.core.errors import ErrorCode
from primee.core.runtime import COMPLETED, DENIED, FAILED
from primee.memory.changelog import count_entries
from primee.memory.initializer import initialize_vault
from primee.memory.page import parse_page

from .support import (
    AllowAllApprovalGate,
    DATA,
    TODAY,
    TempVaultCase,
    make_config,
    make_runtime,
)

MEMORY_MODES = {
    "vault.read": "auto",
    "vault.list": "auto",
    "vault.create": "auto",
    "vault.append": "auto",
    "vault.update": "approval",
    "vault.index": "auto",
    "vault.init": "approval",
    "connector.metrics.read": "auto",
    "connector.email.read": "auto",
    "connector.calendar.read": "auto",
    "connector.trends.read": "auto",
}


def candidate(title="Renew the domain"):
    return {
        "title": title,
        "reason": "It expires this week.",
        "expected_outcome": "The domain is renewed for a year.",
        "completion_condition": "The registrar shows the new expiry date.",
    }


class MemoryRuntimeCase(TempVaultCase):
    def setUp(self):
        super().setUp()
        from .support import fixed_clock

        initialize_vault(self.vault_root, fixed_clock())

    def runtime(self, *, modes=None, connectors=None, dry_run=None, gate=None, **kwargs):
        config = make_config(
            vault_root=self.vault_root,
            modes=MEMORY_MODES if modes is None else modes,
            connectors=connectors or {},
            **kwargs,
        )
        return make_runtime(config, approval_gate=gate, dry_run=dry_run)

    def vault_files(self):
        return sorted(
            p.relative_to(self.vault_root).as_posix()
            for p in self.vault_root.rglob("*")
            if p.is_file()
        )

    def outputs(self):
        return [p for p in self.vault_files() if p.startswith("outputs/")]


class PlanIntegrationTests(MemoryRuntimeCase):
    def test_the_plan_is_stored_as_a_dated_output_page(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [candidate()]})
        self.assertEqual(outcome.status, COMPLETED)
        write = outcome.vault_writes[0]
        self.assertTrue(write.performed)
        self.assertEqual(write.operation, "publish_output")
        self.assertTrue(write.path.startswith(f"outputs/{TODAY}-"))
        self.assertTrue(write.path.endswith("daily-plan.md"))

    def test_the_stored_plan_has_valid_frontmatter(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [candidate()]})
        path = outcome.vault_writes[0].path
        page = parse_page(
            (self.vault_root / path).read_text(encoding="utf-8"), relative_path=path
        )
        self.assertEqual(page.metadata.type, "output_plan")
        self.assertEqual(page.metadata.status, "final")
        self.assertIn("plan", page.metadata.tags)
        self.assertEqual(page.metadata.created, page.metadata.updated)

    def test_running_plan_twice_never_overwrites_the_first(self):
        first = self.runtime().handle("daily plan", inputs={"candidates": [candidate("First")]})
        second = self.runtime().handle("daily plan", inputs={"candidates": [candidate("Second")]})
        self.assertNotEqual(first.vault_writes[0].path, second.vault_writes[0].path)
        self.assertIn("First", (self.vault_root / first.vault_writes[0].path).read_text("utf-8"))
        self.assertEqual(len(self.outputs()), 2)

    def test_plan_reads_candidates_from_a_wiki_page(self):
        runtime = self.runtime()
        runtime.handle(
            "",
            skill_name="vault",
            inputs={
                "operation": "write_wiki",
                "content": (
                    "## Renew the domain\n"
                    "- reason: it expires this week\n"
                    "- expected_outcome: the domain is renewed\n"
                    "- completion_condition: the registrar shows the new date\n"
                ),
                "metadata": {
                    "title": "Plan candidates",
                    "slug": "plan-candidates",
                    "summary": "Approved candidates.",
                },
            },
        )
        outcome = self.runtime().handle("daily plan")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertEqual(
            outcome.result.structured_data["candidate_source"], "wiki/plan-candidates"
        )
        self.assertEqual(
            outcome.result.structured_data["priorities"][0]["title"], "Renew the domain"
        )

    def test_plan_still_refuses_to_invent_priorities(self):
        outcome = self.runtime().handle("daily plan")
        self.assertEqual(outcome.error_code, ErrorCode.INSUFFICIENT_INFORMATION)
        self.assertEqual(self.outputs(), [])


class TrendsIntegrationTests(MemoryRuntimeCase):
    def connectors(self, fixture):
        return {"trends": {"provider": "mock", "fixture": str(DATA / fixture)}}

    def run_trends(self, fixture):
        return self.runtime(
            connectors=self.connectors(fixture),
            trends_sources=["sample-ai-feed", "sample-market-feed"],
        ).handle("what changed")

    def test_the_first_snapshot_is_stored_as_a_dated_output(self):
        outcome = self.run_trends("trends_day1.json")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(outcome.result.structured_data["first_run"])
        write = outcome.vault_writes[0]
        self.assertTrue(write.performed)
        self.assertTrue(write.path.startswith("outputs/"))

    def test_the_second_run_finds_the_previous_snapshot_and_diffs_it(self):
        first = self.run_trends("trends_day1.json")
        outcome = self.run_trends("trends_day2.json")
        data = outcome.result.structured_data
        self.assertFalse(data["first_run"])
        self.assertEqual(data["previous_snapshot"], first.vault_writes[0].path[:-3])
        kinds = sorted({c["change_type"] for c in data["verified_changes"]})
        self.assertEqual(kinds, ["field_changed", "item_added", "item_removed"])

    def test_the_earlier_snapshot_is_never_overwritten(self):
        first = self.run_trends("trends_day1.json")
        before = (self.vault_root / first.vault_writes[0].path).read_text("utf-8")
        self.run_trends("trends_day2.json")
        self.assertEqual(
            (self.vault_root / first.vault_writes[0].path).read_text("utf-8"), before
        )
        self.assertEqual(len(self.outputs()), 2)

    def test_the_snapshot_page_is_a_valid_output_snapshot(self):
        outcome = self.run_trends("trends_day1.json")
        path = outcome.vault_writes[0].path
        page = parse_page((self.vault_root / path).read_text("utf-8"), relative_path=path)
        self.assertEqual(page.metadata.type, "output_snapshot")
        self.assertIn("```json", page.body)

    def test_interpretation_stays_empty(self):
        outcome = self.run_trends("trends_day1.json")
        self.assertEqual(outcome.result.structured_data["interpretation"], [])


class InboxAndMetricsIntegrationTests(MemoryRuntimeCase):
    def test_the_morning_brief_is_stored_as_a_dated_output(self):
        outcome = self.runtime(
            connectors={
                "email": {"provider": "mock", "fixture": str(DATA / "email.json")},
                "calendar": {"provider": "mock", "fixture": str(DATA / "calendar.json")},
            }
        ).handle("morning brief")
        self.assertEqual(outcome.status, COMPLETED)
        write = outcome.vault_writes[0]
        self.assertTrue(write.performed)
        path = write.path
        page = parse_page((self.vault_root / path).read_text("utf-8"), relative_path=path)
        self.assertEqual(page.metadata.type, "output_brief")

    def test_the_brief_never_stores_a_message_body(self):
        outcome = self.runtime(
            connectors={"email": {"provider": "mock", "fixture": str(DATA / "email.json")}}
        ).handle("morning brief")
        text = (self.vault_root / outcome.vault_writes[0].path).read_text("utf-8")
        self.assertIn("Message bodies are never read or stored", text)

    def test_store_false_suppresses_the_write(self):
        outcome = self.runtime(
            connectors={"email": {"provider": "mock", "fixture": str(DATA / "email.json")}}
        ).handle("morning brief", inputs={"store": False})
        self.assertEqual(outcome.result.proposed_vault_writes, [])
        self.assertEqual(self.outputs(), [])

    def test_the_metrics_report_is_stored_as_a_dated_output(self):
        outcome = self.runtime(
            connectors={"metrics": {"provider": "mock", "fixture": str(DATA / "metrics.json")}},
            metrics_series=[
                {"key": "website_visits", "label": "Visits", "summaries": ["latest", "delta"]}
            ],
        ).handle("metrics report")
        self.assertEqual(outcome.status, COMPLETED)
        path = outcome.vault_writes[0].path
        page = parse_page((self.vault_root / path).read_text("utf-8"), relative_path=path)
        self.assertEqual(page.metadata.type, "output_report")
        self.assertIn("1420", page.body)

    def test_metrics_reports_missing_series_in_the_stored_page(self):
        outcome = self.runtime(
            connectors={"metrics": {"provider": "mock", "fixture": str(DATA / "metrics.json")}},
            metrics_series=[
                {"key": "website_visits", "label": "Visits", "summaries": ["latest"]},
                {"key": "revenue", "label": "Revenue", "summaries": ["latest"]},
            ],
        ).handle("metrics report")
        text = (self.vault_root / outcome.vault_writes[0].path).read_text("utf-8")
        self.assertIn("Not available", text)
        self.assertIn("rather than estimating", text)


class PermissionIntegrationTests(MemoryRuntimeCase):
    def test_a_denied_vault_create_stops_the_write(self):
        outcome = self.runtime(modes={"vault.read": "auto", "vault.list": "auto"}).handle(
            "daily plan", inputs={"candidates": [candidate()]}
        )
        write = outcome.vault_writes[0]
        self.assertFalse(write.performed)
        self.assertEqual(write.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertEqual(self.outputs(), [])

    def test_vault_init_needs_approval_by_default(self):
        outcome = self.runtime().handle(
            "", skill_name="vault",
            inputs={"operation": "init", "root": str(self.tmp_path / "New")},
        )
        self.assertEqual(outcome.status, DENIED)
        self.assertEqual(outcome.error_code, ErrorCode.APPROVAL_DENIED)
        self.assertFalse((self.tmp_path / "New").exists())

    def test_init_works_when_no_vault_exists_yet(self):
        """The bootstrap case: init must not require a working Vault."""
        fresh = self.tmp_path / "FirstEverVault"
        config = make_config(vault_root=fresh, modes=MEMORY_MODES)
        runtime = make_runtime(config, approval_gate=AllowAllApprovalGate())
        outcome = runtime.handle(
            "", skill_name="vault", inputs={"operation": "init", "root": str(fresh)}
        )
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue((fresh / "raw").is_dir())
        self.assertTrue((fresh / "INDEX.md").is_file())

    def test_a_dry_run_init_on_a_missing_vault_reports_the_plan(self):
        fresh = self.tmp_path / "PlannedVault"
        config = make_config(vault_root=fresh, modes=MEMORY_MODES)
        runtime = make_runtime(config, approval_gate=AllowAllApprovalGate(), dry_run=True)
        outcome = runtime.handle(
            "", skill_name="vault", inputs={"operation": "init", "root": str(fresh)}
        )
        self.assertEqual(outcome.status, COMPLETED)
        self.assertIn("Would create", outcome.result.summary)
        self.assertFalse(fresh.exists())

    def test_init_refuses_an_unrelated_non_empty_directory(self):
        photos = self.tmp_path / "Photos"
        photos.mkdir()
        (photos / "holiday.jpg").write_text("synthetic", encoding="utf-8")
        config = make_config(vault_root=photos, modes=MEMORY_MODES)
        runtime = make_runtime(config, approval_gate=AllowAllApprovalGate())
        outcome = runtime.handle(
            "", skill_name="vault", inputs={"operation": "init", "root": str(photos)}
        )
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.VAULT_PATH_REJECTED)
        self.assertEqual([p.name for p in photos.iterdir()], ["holiday.jpg"])

    def test_vault_init_succeeds_once_approved(self):
        outcome = self.runtime(gate=AllowAllApprovalGate()).handle(
            "", skill_name="vault",
            inputs={"operation": "init", "root": str(self.tmp_path / "New")},
        )
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue((self.tmp_path / "New" / "raw").is_dir())

    def test_rebuild_index_needs_the_index_permission(self):
        outcome = self.runtime(modes={"vault.read": "auto"}).handle(
            "", skill_name="vault", inputs={"operation": "rebuild_index"}
        )
        self.assertEqual(outcome.status, DENIED)
        self.assertEqual(outcome.error_code, ErrorCode.PERMISSION_DENIED)

    def test_every_vault_operation_maps_to_a_declared_permission(self):
        from primee.memory.operations import OPERATIONS

        registry = self.runtime().registry
        declared = registry.require("vault").manifest.required_permissions
        for spec in OPERATIONS.values():
            self.assertIn(spec.permission, declared, msg=spec.name)


class DryRunIntegrationTests(MemoryRuntimeCase):
    def test_a_dry_run_writes_no_page(self):
        outcome = self.runtime(dry_run=True).handle(
            "daily plan", inputs={"candidates": [candidate()]}
        )
        self.assertTrue(outcome.dry_run)
        self.assertFalse(outcome.vault_writes[0].performed)
        self.assertEqual(outcome.vault_writes[0].state, "dry_run")
        self.assertEqual(self.outputs(), [])

    def test_a_dry_run_leaves_the_changelog_untouched(self):
        before = count_entries((self.vault_root / "CHANGELOG.md").read_text("utf-8"))
        self.runtime(dry_run=True).handle("daily plan", inputs={"candidates": [candidate()]})
        after = count_entries((self.vault_root / "CHANGELOG.md").read_text("utf-8"))
        self.assertEqual(before, after)

    def test_a_dry_run_init_creates_nothing(self):
        outcome = self.runtime(dry_run=True, gate=AllowAllApprovalGate()).handle(
            "", skill_name="vault",
            inputs={"operation": "init", "root": str(self.tmp_path / "Planned")},
        )
        self.assertEqual(outcome.status, COMPLETED)
        self.assertFalse((self.tmp_path / "Planned").exists())
        self.assertIn("Would create", outcome.result.summary)


class GatewayTests(MemoryRuntimeCase):
    def test_no_skill_other_than_vault_declares_a_write_path(self):
        registry = self.runtime().registry
        for skill in registry:
            if skill.name == "vault":
                continue
            prefix = skill.manifest.persistence.path_prefix
            self.assertIn(prefix, ("", "outputs"), msg=skill.name)

    def test_the_changelog_records_the_requesting_skill(self):
        self.runtime().handle("daily plan", inputs={"candidates": [candidate()]})
        text = (self.vault_root / "CHANGELOG.md").read_text("utf-8")
        self.assertIn("publish_output", text)
        self.assertIn("| plan |", text)

    def test_an_unknown_operation_is_refused(self):
        outcome = self.runtime().handle(
            "", skill_name="vault", inputs={"operation": "delete_everything"}
        )
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_INPUT)

    def test_a_traversal_through_the_skill_is_blocked(self):
        outcome = self.runtime().handle(
            "", skill_name="vault",
            inputs={"operation": "create", "path": "../escape.md", "content": "x"},
        )
        self.assertEqual(outcome.error_code, ErrorCode.VAULT_PATH_REJECTED)
        self.assertFalse((self.tmp_path / "escape.md").exists())

    def test_search_and_backlinks_work_through_the_skill(self):
        runtime = self.runtime()
        runtime.handle(
            "", skill_name="vault",
            inputs={
                "operation": "create_raw",
                "content": "A synthetic capture about lighthouses.",
                "metadata": {"title": "Lighthouse capture", "summary": "About lighthouses."},
            },
        )
        found = runtime.handle(
            "", skill_name="vault",
            inputs={"operation": "search_text", "query": "lighthouse"},
        )
        self.assertEqual(found.result.structured_data["count"], 1)


if __name__ == "__main__":
    unittest.main()
