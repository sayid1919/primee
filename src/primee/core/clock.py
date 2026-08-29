"""Time source abstraction.

The local timezone is taken from the operating system at call time so that a
Windows timezone change is picked up without any hardcoded geographic data.
Tests inject :class:`FixedClock` instead of patching global state.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """Local wall clock, timezone-aware, resolved from the operating system."""

    def now(self) -> datetime:
        return datetime.now().astimezone()


class FixedClock:
    """Deterministic clock for tests and dry runs."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        self._moment = moment

    def now(self) -> datetime:
        return self._moment


def today_iso(clock: Clock) -> str:
    """Return the local calendar date as ``YYYY-MM-DD``."""
    return clock.now().date().isoformat()


def timestamp_iso(clock: Clock) -> str:
    """Return an ISO-8601 timestamp including the local UTC offset."""
    return clock.now().isoformat(timespec="seconds")


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(value)
