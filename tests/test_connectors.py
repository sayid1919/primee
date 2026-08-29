"""Connectors are read-only, default to 'not configured', and never raise."""

from __future__ import annotations

import unittest

from primee.connectors.base import (
    CONFIGURED,
    ERROR,
    NOT_CONFIGURED,
    MockCalendarSource,
    MockEmailSource,
    MockMetricsSource,
    MockTrendsSource,
)
from primee.connectors.registry import ConnectorRegistry

from .support import DATA, make_config


class DefaultTests(unittest.TestCase):
    def test_a_fresh_install_has_no_configured_connector(self):
        registry = ConnectorRegistry.from_config(make_config())
        for kind in ("metrics", "email", "calendar", "trends"):
            self.assertFalse(registry.get(kind).configured, msg=kind)

    def test_not_configured_connectors_return_no_data(self):
        registry = ConnectorRegistry.from_config(make_config())
        self.assertEqual(registry.metrics().fetch_series("x", "day").status, NOT_CONFIGURED)
        self.assertEqual(registry.email().fetch_recent(10).status, NOT_CONFIGURED)
        self.assertEqual(registry.calendar().fetch_events("2026-08-27").status, NOT_CONFIGURED)
        self.assertEqual(registry.trends().fetch_snapshot(("a",)).status, NOT_CONFIGURED)
        self.assertIsNone(registry.metrics().fetch_series("x", "day").data)

    def test_an_unknown_provider_name_is_treated_as_not_configured(self):
        config = make_config(connectors={"metrics": {"provider": "some-real-saas"}})
        registry = ConnectorRegistry.from_config(config)
        self.assertFalse(registry.metrics().configured)

    def test_connectors_expose_no_write_method(self):
        registry = ConnectorRegistry.from_config(make_config())
        for kind in ("metrics", "email", "calendar", "trends"):
            connector = registry.get(kind)
            for forbidden in ("send", "write", "create", "delete", "update", "post"):
                self.assertFalse(hasattr(connector, forbidden), msg=f"{kind}.{forbidden}")
            self.assertTrue(connector.describe()["read_only"])


class MockConnectorTests(unittest.TestCase):
    def test_metrics_mock_reads_a_fixture(self):
        source = MockMetricsSource(str(DATA / "metrics.json"))
        response = source.fetch_series("website_visits", "day")
        self.assertEqual(response.status, CONFIGURED)
        self.assertEqual(len(response.data["points"]), 4)
        self.assertEqual(response.data["points"][0]["date"], "2026-08-24")

    def test_metrics_mock_reports_an_unknown_series(self):
        source = MockMetricsSource(str(DATA / "metrics.json"))
        self.assertEqual(source.fetch_series("nope", "day").status, NOT_CONFIGURED)

    def test_email_mock_respects_the_limit(self):
        source = MockEmailSource(str(DATA / "email.json"))
        self.assertEqual(len(source.fetch_recent(2).data["messages"]), 2)

    def test_calendar_mock_filters_by_day(self):
        source = MockCalendarSource(str(DATA / "calendar.json"))
        events = source.fetch_events("2026-08-27").data["events"]
        self.assertEqual({event["date"] for event in events}, {"2026-08-27"})

    def test_trends_mock_reports_missing_sources(self):
        source = MockTrendsSource(str(DATA / "trends_day1.json"))
        response = source.fetch_snapshot(("sample-ai-feed", "not-in-fixture"))
        self.assertEqual(response.data["missing_sources"], ["not-in-fixture"])


class ErrorHandlingTests(unittest.TestCase):
    def test_missing_fixture_becomes_an_error_response_not_an_exception(self):
        response = MockMetricsSource(str(DATA / "does-not-exist.json")).fetch_series("a", "day")
        self.assertEqual(response.status, ERROR)
        self.assertIn("not found", response.message)

    def test_missing_fixture_setting_is_reported_as_not_configured(self):
        response = MockMetricsSource(None).fetch_series("a", "day")
        self.assertEqual(response.status, NOT_CONFIGURED)

    def test_malformed_fixture_data_becomes_an_error_response(self):
        response = MockMetricsSource(str(DATA / "malformed.json")).fetch_series(
            "website_visits", "day"
        )
        self.assertEqual(response.status, ERROR)

    def test_invalid_json_becomes_an_error_response(self):
        source = MockEmailSource(str(DATA / "email.json"))
        source.fixture = str(DATA.parent / "skills_valid" / "alpha" / "SKILL.md")
        self.assertEqual(source.fetch_recent(5).status, ERROR)

    def test_error_messages_are_redacted(self):
        source = MockMetricsSource("/tmp/whatever.json")
        response = source.failure("failed while using api_key=PLACEHOLDERVALUE")
        self.assertNotIn("PLACEHOLDERVALUE", response.message)


class CredentialHygieneTests(unittest.TestCase):
    def test_connector_config_stores_only_an_env_var_name(self):
        config = make_config(
            connectors={"metrics": {"provider": "none", "credential_env": "PRIMEE_METRICS_TOKEN"}}
        )
        self.assertEqual(config.connector("metrics").credential_env, "PRIMEE_METRICS_TOKEN")

    def test_describe_never_exposes_a_credential_field(self):
        registry = ConnectorRegistry.from_config(make_config())
        described = registry.describe()
        self.assertNotIn("credential", str(described).lower())


if __name__ == "__main__":
    unittest.main()
