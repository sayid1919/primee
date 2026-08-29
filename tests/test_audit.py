"""Audit events: sanitized, append-only, and free of message content."""

from __future__ import annotations

import json
import unittest

from primee.core.audit import AuditLog, JsonlSink, MemorySink, NullSink

from .support import TempVaultCase, fixed_clock


class AuditEventTests(unittest.TestCase):
    def setUp(self):
        self.sink = MemorySink()
        self.log = AuditLog(self.sink, fixed_clock())

    def test_records_the_required_fields(self):
        event = self.log.record(
            request_id="req1",
            actor_skill="plan",
            action="vault.create:plans/2026-08-27.md",
            outcome="written",
            approval_state="granted",
            permission="vault.create",
        )
        self.assertEqual(event.timestamp, "2026-08-27T08:00:00+03:30")
        self.assertEqual(event.request_id, "req1")
        self.assertEqual(event.actor_skill, "plan")
        self.assertEqual(event.action, "vault.create:plans/2026-08-27.md")
        self.assertEqual(event.approval_state, "granted")
        self.assertEqual(event.outcome, "written")
        self.assertEqual(event.permission, "vault.create")

    def test_details_are_redacted(self):
        event = self.log.record(
            request_id="req2",
            actor_skill="inbox",
            action="connector.email.read",
            outcome="allowed",
            detail={"api_key": "PLACEHOLDERVALUE", "note": "from sample@example.invalid"},
        )
        self.assertNotIn("PLACEHOLDERVALUE", json.dumps(event.to_dict()))
        self.assertNotIn("sample@example.invalid", json.dumps(event.to_dict()))

    def test_disabled_log_records_nothing_to_the_sink(self):
        log = AuditLog(self.sink, fixed_clock(), enabled=False)
        log.record(request_id="r", actor_skill="x", action="a", outcome="o")
        self.assertEqual(self.sink.events, [])

    def test_null_sink_accepts_everything(self):
        log = AuditLog(NullSink(), fixed_clock())
        log.record(request_id="r", actor_skill="x", action="a", outcome="o")
        self.assertEqual(len(log.events), 1)

    def test_request_ids_are_unique(self):
        ids = {self.log.new_request_id() for _ in range(50)}
        self.assertEqual(len(ids), 50)


class JsonlSinkTests(TempVaultCase):
    def test_appends_one_json_object_per_line(self):
        path = self.tmp_path / "audit" / "audit.jsonl"
        log = AuditLog(JsonlSink(path), fixed_clock())
        for index in range(3):
            log.record(request_id=f"r{index}", actor_skill="plan", action="x", outcome="ok")
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 3)
        for line in lines:
            self.assertIn("timestamp", json.loads(line))

    def test_existing_entries_are_never_rewritten(self):
        path = self.tmp_path / "audit.jsonl"
        log = AuditLog(JsonlSink(path), fixed_clock())
        log.record(request_id="first", actor_skill="a", action="x", outcome="ok")
        first_line = path.read_text(encoding="utf-8").splitlines()[0]
        log.record(request_id="second", actor_skill="b", action="y", outcome="ok")
        self.assertEqual(path.read_text(encoding="utf-8").splitlines()[0], first_line)


if __name__ == "__main__":
    unittest.main()
