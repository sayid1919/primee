"""Configuration loading, defaults and credential hygiene."""

from __future__ import annotations

import os
import unittest

from primee.core.config import ConfigError, PrimeeConfig, expand, load_config, load_mapping

from . import REPO_ROOT
from .support import TempVaultCase

REPO_CONFIG = REPO_ROOT / "config"


def load_examples(destination) -> PrimeeConfig:
    """Copy the shipped *.example.toml templates into a directory and load them.

    The examples are templates, so a plain checkout deliberately loads no
    configuration at all. This helper exercises the templates themselves.
    """
    for source in sorted(REPO_CONFIG.glob("*.example.toml")):
        target = destination / source.name.replace(".example", "")
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    return load_config(destination)


class DefaultsTests(unittest.TestCase):
    def test_no_configuration_yields_safe_defaults(self):
        config = load_config(None)
        self.assertFalse(config.vault.configured)
        self.assertFalse(config.runtime.dry_run)
        self.assertEqual(config.permissions.default_mode, "never")
        self.assertEqual(config.metrics_series, ())
        self.assertEqual(config.trends_sources, ())
        for kind in ("metrics", "email", "calendar", "trends"):
            self.assertFalse(config.connector(kind).configured)

    def test_a_plain_checkout_loads_no_configuration(self):
        # Only *.example.toml is committed, so `primee doctor` on a fresh clone
        # starts from the safe built-in defaults.
        config = load_config(REPO_CONFIG)
        self.assertEqual(config.source_files, ())
        self.assertFalse(config.vault.configured)


class ExampleTemplateTests(TempVaultCase):
    def test_the_shipped_example_files_parse(self):
        config = load_examples(self.tmp_path)
        self.assertIsInstance(config, PrimeeConfig)
        # primee, permissions, connectors and, since Step Three, voice.
        self.assertEqual(len(config.source_files), 4)

    def test_the_example_voice_file_keeps_speech_off(self):
        config = load_examples(self.tmp_path)
        self.assertFalse(config.voice.enabled)
        self.assertEqual(config.voice.tts_engine, "none")
        self.assertFalse(config.voice.configured)
        self.assertFalse(config.voice.save_transcripts)
        self.assertIsNone(config.voice.runtime_dir)
        self.assertIsNone(config.voice.models_dir)
        # The example policy asks before speaking; it never speaks silently.
        self.assertEqual(config.permissions.mode_for("audio.playback"), "approval")

    def test_the_example_policy_is_still_deny_by_default(self):
        policy = load_examples(self.tmp_path).permissions
        self.assertEqual(policy.default_mode, "never")
        self.assertEqual(policy.mode_for("email.send"), "never")
        self.assertEqual(policy.mode_for("vault.update"), "approval")
        self.assertEqual(policy.mode_for("vault.read"), "auto")

    def test_the_example_config_ships_no_vault_path_and_no_provider(self):
        config = load_examples(self.tmp_path)
        self.assertFalse(config.vault.configured)
        for kind in ("metrics", "email", "calendar", "trends"):
            self.assertEqual(config.connector(kind).provider, "none")

    def test_the_example_config_declares_metric_series_without_data(self):
        config = load_examples(self.tmp_path)
        self.assertEqual(
            [series.key for series in config.metrics_series],
            ["website_visits", "new_leads", "conversion_rate"],
        )
        self.assertEqual(config.trends_sources, ())


class ValidationTests(unittest.TestCase):
    def test_rejects_a_bad_time_of_day(self):
        with self.assertRaises(ConfigError):
            load_mapping({"schedule": {"inbox_at": "8am"}})

    def test_rejects_an_unknown_weekday(self):
        with self.assertRaises(ConfigError):
            load_mapping({"schedule": {"rest_days": ["caturday"]}})

    def test_rejects_a_metric_series_without_a_key(self):
        with self.assertRaises(ConfigError):
            load_mapping({"metrics": {"series": [{"label": "no key"}]}})

    def test_rejects_a_metric_series_without_summaries(self):
        with self.assertRaises(ConfigError):
            load_mapping({"metrics": {"series": [{"key": "x", "summaries": []}]}})

    def test_rejects_an_out_of_range_route_threshold(self):
        with self.assertRaises(ConfigError):
            load_mapping({"runtime": {"min_route_score": 5}})

    def test_rejects_a_missing_config_path(self):
        with self.assertRaises(ConfigError):
            load_config("does/not/exist")


class ExpansionTests(unittest.TestCase):
    def test_expands_an_environment_variable_by_name(self):
        os.environ["PRIMEE_TEST_HOME"] = "/synthetic/home"
        try:
            self.assertEqual(expand("${env:PRIMEE_TEST_HOME}/vault"), "/synthetic/home/vault")
        finally:
            del os.environ["PRIMEE_TEST_HOME"]

    def test_an_undefined_variable_expands_to_nothing(self):
        self.assertEqual(expand("${env:PRIMEE_DEFINITELY_UNSET}/x"), "/x")

    def test_expands_the_home_shortcut(self):
        self.assertNotIn("~", expand("~/PrimeeVault"))


class StatePathTests(TempVaultCase):
    def test_state_and_audit_live_outside_the_vault(self):
        config = load_mapping(
            {
                "vault": {"root": str(self.vault_root)},
                "runtime": {"state_dir": str(self.tmp_path / "state")},
            }
        )
        audit_path = str(config.audit_path())
        self.assertNotIn(str(self.vault_root), audit_path)
        self.assertTrue(audit_path.endswith(os.path.join("audit", "audit.jsonl")))


class CredentialHygieneTests(unittest.TestCase):
    def test_no_shipped_config_file_contains_a_credential_value(self):
        import re
        from pathlib import Path

        pattern = re.compile(
            r"(?im)^\s*(?!#)\s*\w*(password|secret|token|api[_-]?key|cookie)\w*\s*=\s*\S"
        )
        for path in REPO_CONFIG.glob("*.toml"):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(pattern.search(text), msg=f"{path} looks like it has a secret")

    def test_credential_env_holds_a_name_not_a_value(self):
        config = load_mapping(
            {"connectors": {"metrics": {"provider": "none", "credential_env": "PRIMEE_X_TOKEN"}}}
        )
        value = config.connector("metrics").credential_env
        self.assertEqual(value, "PRIMEE_X_TOKEN")
        self.assertTrue(value.isupper())


if __name__ == "__main__":
    unittest.main()
