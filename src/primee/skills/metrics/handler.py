"""Metrics skill handler.

Computes only the summaries the user explicitly configured, from data that a
read-only connector actually returned.  A series with no connector, no data or a
connector error is reported as such; it never gets a number.
"""

from __future__ import annotations

from typing import Any

from primee.connectors.base import CONFIGURED, DENIED, NOT_CONFIGURED
from primee.core.context import SkillContext
from primee.core.errors import ErrorCode
from primee.core.result_types import SkillResult

SKILL_NAME = "metrics"
VALID_PERIODS = ("day", "week", "month")
SUPPORTED_SUMMARIES = ("latest", "previous", "sum", "average", "minimum", "maximum", "delta")


def run(context: SkillContext) -> SkillResult:
    period = str(context.input("period", "day")).strip().lower()
    if period not in VALID_PERIODS:
        return SkillResult.fail(
            SKILL_NAME,
            "The requested period is not supported.",
            ErrorCode.INVALID_INPUT,
            f"Period must be one of: {', '.join(VALID_PERIODS)}.",
        )

    configured_series = list(context.config.metrics_series)
    only = str(context.input("series", "")).strip()
    if only:
        configured_series = [s for s in configured_series if s.key == only]
        if not configured_series:
            return SkillResult.fail(
                SKILL_NAME,
                f"No configured metric series named '{only}'.",
                ErrorCode.INVALID_INPUT,
                "Add the series to [[metrics.series]] in your Primee configuration first.",
            )

    if not configured_series:
        return SkillResult.fail(
            SKILL_NAME,
            "No metric series are configured yet.",
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            "Primee will not invent numbers. Declare the series you care about "
            "under [[metrics.series]] in your Primee configuration.",
            structured_data={"period": period, "series": [], "unconfigured": []},
        )

    connector = context.connectors.metrics()
    reported: list[dict[str, Any]] = []
    unconfigured: list[dict[str, str]] = []
    warnings: list[str] = []

    for series in configured_series:
        response = connector.fetch_series(series.key, period)
        if response.status != CONFIGURED:
            unconfigured.append(
                {
                    "key": series.key,
                    "label": series.label,
                    "status": response.status,
                    "message": response.message,
                    "source_id": response.source_id,
                }
            )
            continue

        points = (response.data or {}).get("points", [])
        if not points:
            unconfigured.append(
                {
                    "key": series.key,
                    "label": series.label,
                    "status": NOT_CONFIGURED,
                    "message": "The connector returned no data points for this series.",
                    "source_id": response.source_id,
                }
            )
            continue

        summaries, skipped = _summarise(points, series.summaries)
        for name in skipped:
            warnings.append(
                f"Summary '{name}' configured for '{series.key}' is not supported "
                f"and was skipped. Supported: {', '.join(SUPPORTED_SUMMARIES)}."
            )
        reported.append(
            {
                "key": series.key,
                "label": series.label,
                "unit": series.unit,
                "point_count": len(points),
                "first_date": points[0]["date"],
                "last_date": points[-1]["date"],
                "source_id": response.source_id,
                "observed_at": response.observed_at,
                "summaries": summaries,
            }
        )

    structured = {
        "period": period,
        "generated_at": context.now_iso(),
        "series": reported,
        "unconfigured": unconfigured,
        "connector": connector.describe(),
    }

    if not reported:
        if unconfigured and all(entry["status"] == DENIED for entry in unconfigured):
            return SkillResult.fail(
                SKILL_NAME,
                "Primee is not permitted to read the metrics connector.",
                ErrorCode.PERMISSION_DENIED,
                unconfigured[0]["message"]
                or "The permission policy refuses 'connector.metrics.read'.",
                structured_data=structured,
                warnings=warnings,
            )
        return SkillResult.fail(
            SKILL_NAME,
            "No metrics data source is available, so there is nothing to report.",
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            "Every configured series is missing a working data source. "
            "Primee reports this instead of guessing the numbers.",
            structured_data=structured,
            warnings=warnings,
        )

    lines = [f"Metrics report ({period}):"]
    for entry in reported:
        parts = ", ".join(
            f"{name}={_format(value)}" for name, value in entry["summaries"].items()
        )
        unit = f" {entry['unit']}" if entry["unit"] else ""
        lines.append(f"- {entry['label']}: {parts}{unit}")
    if unconfigured:
        missing = ", ".join(entry["key"] for entry in unconfigured)
        lines.append(f"- Not available (no configured source): {missing}")

    return SkillResult.ok(
        SKILL_NAME,
        "\n".join(lines),
        structured_data=structured,
        warnings=warnings,
    )


def _summarise(points: list[dict], requested: tuple[str, ...]) -> tuple[dict, list[str]]:
    values = [float(point["value"]) for point in points]
    computed: dict[str, float] = {}
    skipped: list[str] = []
    for name in requested:
        if name not in SUPPORTED_SUMMARIES:
            skipped.append(name)
            continue
        if name == "latest":
            computed[name] = values[-1]
        elif name == "previous":
            if len(values) >= 2:
                computed[name] = values[-2]
        elif name == "sum":
            computed[name] = sum(values)
        elif name == "average":
            computed[name] = round(sum(values) / len(values), 4)
        elif name == "minimum":
            computed[name] = min(values)
        elif name == "maximum":
            computed[name] = max(values)
        elif name == "delta":
            if len(values) >= 2:
                computed[name] = round(values[-1] - values[-2], 4)
    return computed, skipped


def _format(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}"
