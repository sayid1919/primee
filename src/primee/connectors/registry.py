"""Builds connectors from configuration.

The default for every kind is the ``NotConfigured*`` implementation, so a fresh
install reports honestly that it has no data sources rather than pretending.
"""

from __future__ import annotations

from typing import Optional

from ..core.config import ConnectorConfig, PrimeeConfig, expand
from .base import (
    CalendarSource,
    Connector,
    EmailSource,
    MetricsSource,
    MockCalendarSource,
    MockEmailSource,
    MockMetricsSource,
    MockTrendsSource,
    NotConfiguredCalendarSource,
    NotConfiguredEmailSource,
    NotConfiguredMetricsSource,
    NotConfiguredTrendsSource,
    TrendsSource,
)

_BUILDERS = {
    "metrics": (NotConfiguredMetricsSource, MockMetricsSource),
    "email": (NotConfiguredEmailSource, MockEmailSource),
    "calendar": (NotConfiguredCalendarSource, MockCalendarSource),
    "trends": (NotConfiguredTrendsSource, MockTrendsSource),
}


class ConnectorRegistry:
    """Resolves a connector kind to a concrete, read-only connector."""

    def __init__(self, connectors: Optional[dict[str, Connector]] = None) -> None:
        self._connectors: dict[str, Connector] = dict(connectors or {})
        for kind, (fallback, _mock) in _BUILDERS.items():
            self._connectors.setdefault(kind, fallback())

    @classmethod
    def from_config(cls, config: PrimeeConfig) -> "ConnectorRegistry":
        built: dict[str, Connector] = {}
        for kind, (fallback, mock) in _BUILDERS.items():
            built[kind] = _build(config.connector(kind), fallback, mock)
        return cls(built)

    def get(self, kind: str) -> Connector:
        connector = self._connectors.get(kind)
        if connector is None:
            fallback = _BUILDERS.get(kind, (None, None))[0]
            if fallback is None:
                raise KeyError(f"Unknown connector kind '{kind}'.")
            connector = fallback()
            self._connectors[kind] = connector
        return connector

    def metrics(self) -> MetricsSource:
        return self.get("metrics")  # type: ignore[return-value]

    def email(self) -> EmailSource:
        return self.get("email")  # type: ignore[return-value]

    def calendar(self) -> CalendarSource:
        return self.get("calendar")  # type: ignore[return-value]

    def trends(self) -> TrendsSource:
        return self.get("trends")  # type: ignore[return-value]

    def describe(self) -> dict:
        return {kind: connector.describe() for kind, connector in sorted(self._connectors.items())}


def _build(conf: ConnectorConfig, fallback, mock) -> Connector:
    provider = (conf.provider or "none").strip().lower()
    if provider in ("", "none"):
        return fallback()
    if provider == "mock":
        fixture = conf.options.get("fixture")
        return mock(expand(str(fixture)) if fixture else None)
    # An unknown provider name is treated as "not configured" rather than as an
    # error: Step One ships no real providers, and guessing would be worse.
    return fallback()
