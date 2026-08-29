"""Behaviour of the five bundled skills, using synthetic fixtures only."""

from __future__ import annotations

import json
import unittest

from primee.core.errors import ErrorCode
from primee.core.runtime import COMPLETED, FAILED

from .support import DATA, TODAY, TempVaultCase, make_config, make_runtime

METRICS_SERIES = [
    {
        "key": "website_visits",
        "label": "Website visits",
        "unit": "visits",
        "summaries": ["latest", "previous", "delta", "average"],
    },
    {"key": "new_leads", "label": "New leads", "summaries": ["latest", "sum"]},
    {"key": "revenue", "label": "Revenue", "summaries": ["latest"]},
]


def _candidate(title, **overrides):
    candidate = {
        "title": title,
        "reason": "It unblocks the rest of the week.",
        "expected_outcome": "The task is finished and verified.",
        "completion_condition": "The checklist item is ticked.",
    }
    candidate.update(overrides)
    return candidate


class MetricsSkillTests(TempVaultCase):
    def runtime(self, provider="mock", series=METRICS_SERIES):
        connectors = {"metrics": {"provider": provider, "fixture": str(DATA / "metrics.json")}}
        return make_runtime(
            make_config(
                vault_root=self.vault_root, connectors=connectors, metrics_series=series
            )
        )

    def test_reports_only_configured_summaries(self):
        outcome = self.runtime().handle("metrics report")
        self.assertEqual(outcome.status, COMPLETED)
        visits = outcome.result.structured_data["series"][0]
        self.assertEqual(sorted(visits["summaries"]), ["average", "delta", "latest", "previous"])
        self.assertEqual(visits["summaries"]["latest"], 1420.0)
        self.assertEqual(visits["summaries"]["delta"], 145.0)

    def test_never_invents_a_missing_series(self):
        outcome = self.runtime().handle("metrics report")
        unconfigured = outcome.result.structured_data["unconfigured"]
        self.assertEqual([entry["key"] for entry in unconfigured], ["revenue"])
        self.assertNotIn("revenue", json.dumps(outcome.result.structured_data["series"]))
        self.assertIn("Not available", outcome.result.summary)

    def test_no_connector_means_an_explicit_failure(self):
        outcome = self.runtime(provider="none").handle("metrics report")
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.CONNECTOR_NOT_CONFIGURED)
        self.assertEqual(outcome.result.structured_data["series"], [])

    def test_no_configured_series_means_an_explicit_failure(self):
        outcome = self.runtime(series=[]).handle("metrics report")
        self.assertEqual(outcome.error_code, ErrorCode.CONNECTOR_NOT_CONFIGURED)
        self.assertIn("will not invent", outcome.result.sanitized_error_message)

    def test_proposes_one_dated_output_and_no_external_actions(self):
        outcome = self.runtime().handle("metrics report")
        writes = outcome.result.proposed_vault_writes
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0].operation, "publish_output")
        self.assertEqual(writes[0].path, "")  # the Vault generates the path
        self.assertEqual(writes[0].metadata["output_type"], "output_report")
        self.assertEqual(outcome.result.proposed_external_actions, [])

    def test_store_false_proposes_nothing(self):
        outcome = self.runtime().handle("metrics report", inputs={"store": False})
        self.assertEqual(outcome.result.proposed_vault_writes, [])

    def test_invalid_period_is_rejected(self):
        outcome = self.runtime().handle("metrics report", inputs={"period": "century"})
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_INPUT)

    def test_unsupported_summary_is_skipped_with_a_warning(self):
        series = [{"key": "new_leads", "label": "Leads", "summaries": ["latest", "median"]}]
        outcome = self.runtime(series=series).handle("metrics report")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(any("median" in w for w in outcome.result.warnings))


class InboxSkillTests(TempVaultCase):
    def runtime(self, email="mock", calendar="mock"):
        connectors = {
            "email": {"provider": email, "fixture": str(DATA / "email.json")},
            "calendar": {"provider": calendar, "fixture": str(DATA / "calendar.json")},
        }
        return make_runtime(make_config(vault_root=self.vault_root, connectors=connectors))

    def test_returns_at_most_three_items(self):
        outcome = self.runtime().handle("morning brief")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertLessEqual(len(outcome.result.structured_data["items"]), 3)

    def test_every_item_explains_why_it_needs_attention(self):
        outcome = self.runtime().handle("morning brief")
        for item in outcome.result.structured_data["items"]:
            self.assertTrue(item["reason"].startswith("Needs attention because"))
            self.assertTrue(item["score_breakdown"])

    def test_items_are_ranked_by_score(self):
        items = self.runtime().handle("morning brief").result.structured_data["items"]
        scores = [item["score"] for item in items]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(items[0]["id"], "m-001")

    def test_is_read_only_towards_email_and_calendar(self):
        outcome = self.runtime().handle("morning brief")
        self.assertTrue(outcome.result.structured_data["read_only"])
        # Inbox never acts on the mailbox or the calendar...
        self.assertEqual(outcome.result.proposed_external_actions, [])
        # ...and the only thing it writes is its own brief, into outputs/.
        writes = outcome.result.proposed_vault_writes
        self.assertEqual([w.operation for w in writes], ["publish_output"])
        self.assertEqual(writes[0].metadata["output_type"], "output_brief")
        # The brief, plus the changelog entry recording that it was written.
        self.assertEqual(
            sorted(p.name for p in self.vault_root.iterdir()), ["CHANGELOG.md", "outputs"]
        )

    def test_store_false_writes_nothing_at_all(self):
        outcome = self.runtime().handle("morning brief", inputs={"store": False})
        self.assertEqual(outcome.result.proposed_vault_writes, [])
        self.assertEqual(list(self.vault_root.iterdir()), [])

    def test_no_connector_means_no_invented_brief(self):
        outcome = self.runtime(email="none", calendar="none").handle("morning brief")
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.CONNECTOR_NOT_CONFIGURED)
        self.assertEqual(outcome.result.structured_data["items"], [])

    def test_one_missing_source_degrades_with_a_warning(self):
        outcome = self.runtime(calendar="none").handle("morning brief")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(any("Calendar source unavailable" in w for w in outcome.result.warnings))
        kinds = {item["kind"] for item in outcome.result.structured_data["items"]}
        self.assertEqual(kinds, {"email"})

    def test_a_request_to_send_mail_is_vetoed_by_exclusions(self):
        outcome = self.runtime().handle("send email about the morning brief")
        self.assertNotEqual(outcome.route.skill_name, "inbox")


class TrendsSkillTests(TempVaultCase):
    def runtime(self, fixture="trends_day1.json", provider="mock"):
        connectors = {"trends": {"provider": provider, "fixture": str(DATA / fixture)}}
        return make_runtime(
            make_config(
                vault_root=self.vault_root,
                connectors=connectors,
                trends_sources=["sample-ai-feed", "sample-market-feed"],
            )
        )

    def test_first_run_records_a_baseline_and_reports_no_changes(self):
        outcome = self.runtime().handle("what changed")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertTrue(outcome.result.structured_data["first_run"])
        self.assertEqual(outcome.result.structured_data["verified_changes"], [])
        write = outcome.vault_writes[0]
        self.assertTrue(write.performed)
        self.assertTrue(write.path.startswith("outputs/"))
        self.assertTrue((self.vault_root / write.path).is_file())

    def test_second_run_reports_only_real_changes(self):
        self.runtime("trends_day1.json").handle("what changed")
        outcome = self.runtime("trends_day2.json").handle("what changed")
        changes = outcome.result.structured_data["verified_changes"]
        kinds = sorted({change["change_type"] for change in changes})
        self.assertEqual(kinds, ["field_changed", "item_added", "item_removed"])
        added = [c for c in changes if c["change_type"] == "item_added"]
        self.assertEqual([c["item_id"] for c in added], ["a3"])
        removed = [c for c in changes if c["change_type"] == "item_removed"]
        self.assertEqual([c["item_id"] for c in removed], ["a1"])

    def test_unchanged_source_produces_no_change_entries(self):
        self.runtime("trends_day1.json").handle("what changed")
        outcome = self.runtime("trends_day2.json").handle("what changed")
        sources = {c["source_id"] for c in outcome.result.structured_data["verified_changes"]}
        self.assertNotIn("sample-market-feed", sources)

    def test_interpretation_is_empty_and_separate_from_observation(self):
        outcome = self.runtime().handle("what changed")
        data = outcome.result.structured_data
        self.assertEqual(data["interpretation"], [])
        self.assertIn("does not interpret", data["interpretation_note"])

    def test_every_change_records_source_and_observation_times(self):
        self.runtime("trends_day1.json").handle("what changed")
        outcome = self.runtime("trends_day2.json").handle("what changed")
        for change in outcome.result.structured_data["verified_changes"]:
            self.assertTrue(change["source_id"])
            self.assertEqual(change["previous_observed_at"], "2026-08-26T07:30:00+03:30")
            self.assertEqual(change["current_observed_at"], "2026-08-27T07:30:00+03:30")
            self.assertEqual(change["evidence"], "observed_diff")

    def test_no_sources_configured_is_an_explicit_failure(self):
        runtime = make_runtime(make_config(vault_root=self.vault_root, trends_sources=[]))
        outcome = runtime.handle("what changed")
        self.assertEqual(outcome.error_code, ErrorCode.CONNECTOR_NOT_CONFIGURED)

    def test_no_connector_is_an_explicit_failure(self):
        outcome = self.runtime(provider="none").handle("what changed")
        self.assertEqual(outcome.error_code, ErrorCode.CONNECTOR_NOT_CONFIGURED)
        self.assertEqual(outcome.result.structured_data["verified_changes"], [])

    def test_snapshot_file_is_markdown_with_frontmatter(self):
        outcome = self.runtime().handle("what changed")
        text = (self.vault_root / outcome.vault_writes[0].path).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn('type: "output_snapshot"', text)
        self.assertIn("```json", text)


class PlanSkillTests(TempVaultCase):
    def runtime(self, **kwargs):
        return make_runtime(make_config(vault_root=self.vault_root, **kwargs))

    def test_produces_three_priorities_with_the_required_fields(self):
        outcome = self.runtime().handle(
            "daily plan",
            inputs={"candidates": [_candidate(f"Task {n}") for n in range(1, 6)]},
        )
        priorities = outcome.result.structured_data["priorities"]
        self.assertEqual(len(priorities), 3)
        for priority in priorities:
            for field in ("title", "reason", "expected_outcome", "completion_condition"):
                self.assertTrue(priority[field], msg=field)

    def test_uses_an_iso_date_filename(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("A")]})
        path = outcome.vault_writes[0].path
        self.assertEqual(path, f"outputs/{TODAY}-080000-daily-plan.md")
        self.assertTrue((self.vault_root / path).is_file())

    def test_writes_through_the_vault_never_directly(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("A")]})
        write = outcome.vault_writes[0]
        self.assertTrue(write.performed)
        self.assertEqual(write.requested_by, "plan")
        self.assertEqual(write.operation, "publish_output")
        # The skill proposes no path at all; the Vault generates it.
        self.assertEqual(outcome.result.proposed_vault_writes[0].path, "")

    def test_refuses_to_invent_priorities_when_there_is_nothing_to_go_on(self):
        outcome = self.runtime().handle("daily plan")
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(outcome.error_code, ErrorCode.INSUFFICIENT_INFORMATION)
        self.assertEqual(outcome.result.structured_data["priorities"], [])
        self.assertFalse((self.vault_root / "plans").exists())

    def test_incomplete_candidates_are_rejected_not_completed(self):
        outcome = self.runtime().handle(
            "daily plan", inputs={"candidates": [{"title": "Only a title"}]}
        )
        self.assertEqual(outcome.error_code, ErrorCode.INSUFFICIENT_INFORMATION)
        self.assertTrue(any("missing" in w for w in outcome.result.warnings))

    def test_does_not_pad_a_short_candidate_list(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("Only one")]})
        self.assertEqual(len(outcome.result.structured_data["priorities"]), 1)
        self.assertTrue(any("did not pad" in w for w in outcome.result.warnings))

    def test_a_second_plan_never_overwrites_the_first(self):
        first = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("First")]})
        second = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("Second")]})
        first_path, second_path = first.vault_writes[0].path, second.vault_writes[0].path
        self.assertNotEqual(first_path, second_path)
        self.assertIn("First", (self.vault_root / first_path).read_text(encoding="utf-8"))
        self.assertIn("Second", (self.vault_root / second_path).read_text(encoding="utf-8"))

    def test_reads_candidates_from_the_vault_when_none_are_supplied(self):
        self._write_candidates_page()
        outcome = self.runtime().handle("daily plan")
        self.assertEqual(outcome.status, COMPLETED)
        self.assertEqual(
            outcome.result.structured_data["candidate_source"], "wiki/plan-candidates"
        )
        self.assertEqual(
            outcome.result.structured_data["priorities"][0]["title"], "Renew the domain"
        )

    def _write_candidates_page(self):
        (self.vault_root / "wiki").mkdir(exist_ok=True)
        (self.vault_root / "wiki" / "plan-candidates.md").write_text(
            "---\n"
            'id: "wik-20260827-0123456789"\n'
            "schema_version: 1\n"
            'title: "Plan candidates"\n'
            'type: "wiki_topic"\n'
            "tags:\n  - \"plan\"\n"
            'created: "2026-08-27T08:00:00+03:30"\n'
            'updated: "2026-08-27T08:00:00+03:30"\n'
            'summary: "Approved candidate actions."\n'
            "---\n\n"
            "## Renew the domain\n"
            "- reason: it expires this week\n"
            "- expected_outcome: the domain is renewed for a year\n"
            "- completion_condition: the registrar shows the new expiry date\n",
            encoding="utf-8",
        )

    def test_invalid_date_is_rejected(self):
        outcome = self.runtime().handle("daily plan", inputs={"day": "27-08-2026"})
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_INPUT)

    def test_plan_file_is_markdown_with_valid_frontmatter(self):
        outcome = self.runtime().handle("daily plan", inputs={"candidates": [_candidate("A")]})
        text = (self.vault_root / outcome.vault_writes[0].path).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn('type: "output_plan"', text)
        self.assertIn(f'created: "{TODAY}T08:00:00+03:30"', text)


class VaultSkillTests(TempVaultCase):
    def runtime(self, **kwargs):
        return make_runtime(make_config(vault_root=self.vault_root, **kwargs))

    def call(self, runtime, **inputs):
        return runtime.handle("", skill_name="vault", inputs=inputs)

    def test_create_read_and_list_round_trip(self):
        runtime = self.runtime()
        self.assertEqual(
            self.call(runtime, operation="create", path="notes/a.md", content="hello").status,
            COMPLETED,
        )
        read = self.call(runtime, operation="read", path="notes/a.md")
        self.assertEqual(read.result.structured_data["content"], "hello")
        listing = self.call(runtime, operation="list")
        self.assertEqual(listing.result.structured_data["paths"], ["notes/a.md"])

    def test_traversal_is_refused_through_the_skill_too(self):
        outcome = self.call(self.runtime(), operation="create", path="../escape.md", content="x")
        self.assertEqual(outcome.error_code, ErrorCode.VAULT_PATH_REJECTED)
        self.assertFalse((self.tmp_path / "escape.md").exists())

    def test_unknown_operation_is_refused(self):
        outcome = self.call(self.runtime(), operation="delete", path="a.md")
        self.assertEqual(outcome.error_code, ErrorCode.INVALID_INPUT)

    def test_credential_like_content_is_refused(self):
        outcome = self.call(
            self.runtime(), operation="create", path="a.md", content="api_key = PLACEHOLDER"
        )
        self.assertEqual(outcome.error_code, ErrorCode.VAULT_CONTENT_REJECTED)

    def test_a_direct_call_still_passes_through_the_permission_layer(self):
        runtime = self.runtime(modes={"vault.read": "auto"})
        outcome = self.call(runtime, operation="create", path="a.md", content="x")
        self.assertEqual(outcome.error_code, ErrorCode.PERMISSION_DENIED)
        self.assertFalse((self.vault_root / "a.md").exists())

    def test_the_requesting_skill_is_recorded(self):
        runtime = self.runtime()
        outcome = self.call(
            runtime, operation="create", path="a.md", content="x", requested_by="plan"
        )
        self.assertEqual(outcome.result.structured_data["requested_by"], "plan")


if __name__ == "__main__":
    unittest.main()
