"""The local command line interface: exit codes, JSON output, dry-run flag."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from primee.cli import main, parse_inputs
from primee.core.errors import PrimeeError

from . import REPO_ROOT
from .support import DATA, TempVaultCase, TODAY

CONFIG = str(REPO_ROOT / "config")


def run_cli(argv: list[str]) -> tuple[int, str]:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


class CommandTests(unittest.TestCase):
    def test_list_skills_returns_the_five_bundled_skills(self):
        code, output = run_cli(["--config", CONFIG, "list-skills", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(
            sorted(skill["name"] for skill in payload["skills"]),
            ["inbox", "metrics", "plan", "trends", "vault"],
        )
        self.assertEqual(payload["errors"], [])

    def test_permissions_lists_implemented_and_reserved_capabilities(self):
        code, output = run_cli(["permissions", "--json"])
        self.assertEqual(code, 0)
        rows = {row["name"]: row for row in json.loads(output)}
        self.assertTrue(rows["vault.read"]["implemented"])
        self.assertFalse(rows["email.send"]["implemented"])
        self.assertFalse(rows["audit.write"]["skill_requestable"])

    def test_doctor_reports_the_current_state(self):
        code, output = run_cli(["doctor", "--config", CONFIG, "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["hosted_ai_dependencies"], [])
        self.assertEqual(payload["vault"], "not configured")

    def test_explain_shows_the_routing_scores_without_running_anything(self):
        code, output = run_cli(["explain", "morning brief", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output)
        self.assertEqual(payload["skill_name"], "inbox")
        self.assertTrue(payload["all_scores"])

    def test_explain_reports_ambiguity_with_a_non_zero_exit_code(self):
        code, output = run_cli(["explain", "please defragment the toaster", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(output)["status"], "no_match")

    def test_unknown_command_exits_with_an_error(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["not-a-command"])


class ClockIndependenceTests(TempVaultCase):
    """The CLI uses the real system clock, so no CLI test may assume a date.

    An earlier version of this module asserted `plans/<fixture-date>.md` without
    pinning `day`, and started failing the moment the calendar moved on.
    """

    def test_the_plan_path_follows_the_day_input_not_the_wall_clock(self):
        config = self.write_config()
        run_cli(
            [
                "--config",
                config,
                "run",
                "daily plan",
                "--input",
                "day=2001-01-01",
                "--input",
                'candidates=[{"title":"A","reason":"b","expected_outcome":"c","completion_condition":"d"}]',
            ]
        )
        self.assertTrue((self.vault_root / "plans" / "2001-01-01.md").is_file())

    def write_config(self) -> str:
        return RunCommandTests.write_config(self)


class InputParsingTests(unittest.TestCase):
    def test_parses_json_values_when_possible(self):
        parsed = parse_inputs(["limit=2", "overwrite=true", "day=2026-08-27", "tags=[\"a\"]"])
        self.assertEqual(parsed["limit"], 2)
        self.assertIs(parsed["overwrite"], True)
        self.assertEqual(parsed["day"], "2026-08-27")
        self.assertEqual(parsed["tags"], ["a"])

    def test_rejects_a_malformed_pair(self):
        with self.assertRaises(PrimeeError):
            parse_inputs(["justakey"])


class RunCommandTests(TempVaultCase):
    def write_config(self, **extra) -> str:
        lines = [
            "[vault]",
            f'root = "{self.vault_root.as_posix()}"',
            "",
            "[audit]",
            "enabled = true",
            f'path = "{(self.tmp_path / "audit.jsonl").as_posix()}"',
            "",
            "[permissions]",
            'default = "never"',
            "",
            "[permissions.modes]",
            '"vault.read" = "auto"',
            '"vault.list" = "auto"',
            '"vault.create" = "auto"',
            '"vault.append" = "auto"',
            '"vault.update" = "approval"',
            '"connector.email.read" = "auto"',
            '"connector.calendar.read" = "auto"',
            '"connector.metrics.read" = "auto"',
            '"connector.trends.read" = "auto"',
            "",
            "[connectors.email]",
            'provider = "mock"',
            f'fixture = "{(DATA / "email.json").as_posix()}"',
            "",
            "[connectors.calendar]",
            'provider = "mock"',
            f'fixture = "{(DATA / "calendar.json").as_posix()}"',
        ]
        lines.extend(extra.get("extra_lines", []))
        path = self.tmp_path / "primee.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_run_produces_a_morning_brief_from_synthetic_data(self):
        code, output = run_cli(["--config", self.write_config(), "run", "morning brief"])
        self.assertEqual(code, 0)
        self.assertIn("Morning brief", output)

    def test_run_refuses_to_guess_an_ambiguous_request(self):
        code, output = run_cli(["--config", self.write_config(), "run", "defragment the toaster"])
        self.assertEqual(code, 1)
        self.assertIn("not confident enough to guess", output)

    def test_dry_run_writes_nothing(self):
        config = self.write_config()
        code, output = run_cli(
            [
                "--config",
                config,
                "run",
                "daily plan",
                "--dry-run",
                "--input",
                f"day={TODAY}",
                "--input",
                'candidates=[{"title":"A","reason":"b","expected_outcome":"c","completion_condition":"d"}]',
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("[dry run]", output)
        self.assertFalse((self.vault_root / "plans").exists())

    def test_a_real_run_writes_through_the_vault_and_audits_it(self):
        config = self.write_config()
        code, _ = run_cli(
            [
                "--config",
                config,
                "run",
                "daily plan",
                "--input",
                f"day={TODAY}",
                "--input",
                'candidates=[{"title":"A","reason":"b","expected_outcome":"c","completion_condition":"d"}]',
            ]
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.vault_root / "plans" / f"{TODAY}.md").is_file())
        audit_lines = (self.tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        actions = [json.loads(line)["action"] for line in audit_lines]
        self.assertIn("route", actions)
        self.assertTrue(any(action.startswith("vault.create") for action in actions))

    def test_no_audit_flag_keeps_the_log_file_untouched(self):
        config = self.write_config()
        run_cli(["--config", config, "run", "morning brief", "--no-audit"])
        self.assertFalse((self.tmp_path / "audit.jsonl").exists())

    def test_approve_flag_only_grants_the_named_permission(self):
        config = self.write_config()
        candidates = 'candidates=[{"title":"A","reason":"b","expected_outcome":"c","completion_condition":"d"}]'
        day = f"day={TODAY}"
        run_cli(["--config", config, "run", "daily plan", "--input", day, "--input", candidates])
        code, output = run_cli(
            [
                "--config",
                config,
                "run",
                "daily plan",
                "--input",
                day,
                "--input",
                candidates,
                "--input",
                "overwrite=true",
                "--approve",
                "vault.update",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("written", output)


if __name__ == "__main__":
    unittest.main()
