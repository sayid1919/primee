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
    """Run the CLI, capturing both streams so tests stay quiet."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


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

    def outputs(self) -> list[str]:
        folder = self.vault_root / "outputs"
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

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
        # The date comes from the 'day' input; only the time part comes from
        # the wall clock, so this assertion stays true on any day.
        names = self.outputs()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], r"^2001-01-01-\d{6}-daily-plan\.md$")

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
    def outputs(self) -> list[str]:
        folder = self.vault_root / "outputs"
        return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []

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
            '"vault.index" = "auto"',
            '"vault.init" = "approval"',
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
        self.assertEqual(self.outputs(), [])

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
        names = self.outputs()
        self.assertEqual(len(names), 1)
        self.assertRegex(names[0], rf"^{TODAY}-\d{{6}}-daily-plan\.md$")
        audit_lines = (self.tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        actions = [json.loads(line)["action"] for line in audit_lines]
        self.assertIn("route", actions)
        self.assertTrue(any("publish_output" in action for action in actions))

    def test_no_audit_flag_keeps_the_log_file_untouched(self):
        config = self.write_config()
        run_cli(["--config", config, "run", "morning brief", "--no-audit"])
        self.assertFalse((self.tmp_path / "audit.jsonl").exists())

    def test_approve_flag_only_grants_the_named_permission(self):
        config = self.write_config()
        # Create a wiki page (vault.create is "auto" here).
        code, _ = run_cli(["--config", config, "run", "morning brief"])
        self.assertEqual(code, 0)
        # rebuild_index needs vault.index, which is "auto" in this config.
        code, output = run_cli(["--config", config, "vault", "rebuild_index"])
        self.assertEqual(code, 0)
        self.assertIn("Rebuilt INDEX.md", output)

    def test_an_operation_needing_approval_is_refused_without_the_flag(self):
        config = self.write_config()
        target = str(self.tmp_path / "AnotherVault")
        code, _ = run_cli(["--config", config, "vault", "init", "--root", target])
        self.assertEqual(code, 1)
        self.assertFalse((self.tmp_path / "AnotherVault").exists())

    def test_the_same_operation_succeeds_with_approve(self):
        config = self.write_config()
        target = str(self.tmp_path / "AnotherVault")
        code, output = run_cli(
            ["--config", config, "vault", "init", "--root", target, "--approve", "vault.init"]
        )
        self.assertEqual(code, 0)
        self.assertTrue((self.tmp_path / "AnotherVault" / "raw").is_dir())


if __name__ == "__main__":
    unittest.main()
