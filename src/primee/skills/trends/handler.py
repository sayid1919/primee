"""Trends skill handler.

The comparison is a plain set and field diff between two snapshots.  Everything
it reports is something Primee observed; nothing is inferred.  The snapshot file
is Markdown with YAML frontmatter, so the Vault stays human readable, with the
machine readable payload in a fenced JSON block.
"""

from __future__ import annotations

import json
from typing import Any

from primee.connectors.base import CONFIGURED, DENIED, ERROR
from primee.core.context import SkillContext
from primee.core.errors import ErrorCode
from primee.core.frontmatter import parse_document
from primee.core.result_types import SkillResult, VaultWrite

SKILL_NAME = "trends"
DEFAULT_SNAPSHOT_PATH = "trends/latest-snapshot.md"
SNAPSHOT_VERSION = 1
FENCE = "```json"


def run(context: SkillContext) -> SkillResult:
    requested = context.input("sources", None)
    if requested is None:
        source_ids = tuple(context.config.trends_sources)
    elif isinstance(requested, (list, tuple)):
        source_ids = tuple(str(item).strip() for item in requested if str(item).strip())
    else:
        source_ids = tuple(
            part.strip() for part in str(requested).split(",") if part.strip()
        )

    if not source_ids:
        return SkillResult.fail(
            SKILL_NAME,
            "No trend sources are configured yet.",
            ErrorCode.CONNECTOR_NOT_CONFIGURED,
            "Add the sources you want watched to 'trends.sources' in your "
            "Primee configuration. Primee will not pick sources on its own.",
        )

    connector = context.connectors.trends()
    response = connector.fetch_snapshot(source_ids)
    if response.status != CONFIGURED:
        if response.status == DENIED:
            failure_code = ErrorCode.PERMISSION_DENIED
        elif response.status == ERROR:
            failure_code = ErrorCode.CONNECTOR_ERROR
        else:
            failure_code = ErrorCode.CONNECTOR_NOT_CONFIGURED
        return SkillResult.fail(
            SKILL_NAME,
            "No trends data source is available, so no changes can be reported.",
            failure_code,
            response.message or "The trends connector returned no data.",
            structured_data={
                "sources": list(source_ids),
                "connector": connector.describe(),
                "verified_changes": [],
                "interpretation": [],
            },
        )

    current_items = (response.data or {}).get("items", {})
    missing_sources = (response.data or {}).get("missing_sources", [])
    observed_at = response.observed_at or context.now_iso()

    snapshot_path = str(context.input("snapshot_path", DEFAULT_SNAPSHOT_PATH)).strip()
    previous, previous_error = _load_previous(context, snapshot_path)

    warnings: list[str] = []
    if missing_sources:
        warnings.append(
            "Configured source(s) returned nothing: " + ", ".join(missing_sources) + "."
        )
    if previous_error:
        warnings.append(previous_error)

    first_run = previous is None
    verified = [] if first_run else _diff(previous.get("items", {}), current_items, previous.get("observed_at", ""), observed_at)

    snapshot_payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "observed_at": observed_at,
        "source_id": response.source_id,
        "items": current_items,
    }
    document = _render_snapshot(snapshot_payload, context.today())
    operation = "create" if first_run and previous_error is None else "update"
    if first_run and previous_error is not None:
        operation = "update"

    structured: dict[str, Any] = {
        "generated_at": context.now_iso(),
        "first_run": first_run,
        "snapshot_path": snapshot_path,
        "previous_observed_at": "" if first_run else previous.get("observed_at", ""),
        "current_observed_at": observed_at,
        "sources": list(source_ids),
        "missing_sources": list(missing_sources),
        "connector": connector.describe(),
        "verified_changes": verified,
        "interpretation": [],
        "interpretation_note": (
            "Primee Step One has no reasoning engine and does not interpret "
            "changes. This list is intentionally empty."
        ),
    }

    if first_run:
        summary = (
            "This is the first trends run, so there is no previous snapshot to "
            f"compare against. Recorded {sum(len(v) for v in current_items.values())} "
            f"item(s) across {len(current_items)} source(s) as the new baseline."
        )
    elif not verified:
        summary = (
            f"No changes since the previous snapshot taken at "
            f"{previous.get('observed_at', 'an earlier time')}."
        )
    else:
        lines = [f"{len(verified)} verified change(s) since the previous snapshot:"]
        for change in verified[:10]:
            lines.append(
                f"- [{change['source_id']}] {change['change_type']}: {change['title']}"
            )
        if len(verified) > 10:
            lines.append(f"- ...and {len(verified) - 10} more.")
        summary = "\n".join(lines)

    return SkillResult.ok(
        SKILL_NAME,
        summary,
        structured_data=structured,
        warnings=warnings,
        proposed_vault_writes=[
            VaultWrite(
                path=snapshot_path,
                operation=operation,
                content=document,
                reason="Store the observed snapshot so the next run has a baseline.",
            )
        ],
    )


def _load_previous(context: SkillContext, path: str) -> tuple[dict | None, str | None]:
    read = context.vault.read(path)
    if not read.ok:
        if read.error_code == ErrorCode.VAULT_FILE_MISSING:
            return None, None
        return None, (
            f"The previous snapshot could not be read ({read.error_code}); "
            "this run is treated as a first run."
        )
    payload = _extract_json(read.content or "")
    if payload is None:
        return None, (
            "The stored snapshot is not readable as a Primee snapshot; "
            "this run is treated as a first run."
        )
    return payload, None


def _extract_json(document: str) -> dict | None:
    try:
        _meta, body = parse_document(document)
    except Exception:
        body = document
    start = body.find(FENCE)
    if start == -1:
        return None
    start += len(FENCE)
    end = body.find("```", start)
    if end == -1:
        return None
    try:
        payload = json.loads(body[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), dict):
        return None
    return payload


def _render_snapshot(payload: dict, day: str) -> str:
    frontmatter = [
        "---",
        "primee_type: trends_snapshot",
        f"snapshot_version: {payload['snapshot_version']}",
        f'observed_at: "{payload["observed_at"]}"',
        f'source_id: "{payload["source_id"]}"',
        f'recorded_on: "{day}"',
        "---",
        "",
        "# Trends snapshot",
        "",
        "Machine readable payload. Do not edit by hand.",
        "",
        FENCE,
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ]
    return "\n".join(frontmatter)


def _diff(previous: dict, current: dict, previous_at: str, current_at: str) -> list[dict]:
    changes: list[dict] = []
    for source_id in sorted(set(previous) | set(current)):
        old_items = {str(item.get("id", "")): item for item in previous.get(source_id, []) if isinstance(item, dict)}
        new_items = {str(item.get("id", "")): item for item in current.get(source_id, []) if isinstance(item, dict)}

        if source_id not in previous:
            changes.append(
                _change(source_id, "source_added", f"New source '{source_id}' is now being watched.", previous_at, current_at)
            )
        if source_id not in current:
            changes.append(
                _change(source_id, "source_removed", f"Source '{source_id}' is no longer being watched.", previous_at, current_at)
            )
            continue

        for item_id in sorted(set(new_items) - set(old_items)):
            item = new_items[item_id]
            changes.append(
                _change(
                    source_id,
                    "item_added",
                    str(item.get("title", item_id)),
                    previous_at,
                    current_at,
                    item_id=item_id,
                    published_at=str(item.get("published_at", "")),
                )
            )
        for item_id in sorted(set(old_items) - set(new_items)):
            item = old_items[item_id]
            changes.append(
                _change(
                    source_id,
                    "item_removed",
                    str(item.get("title", item_id)),
                    previous_at,
                    current_at,
                    item_id=item_id,
                )
            )
        for item_id in sorted(set(old_items) & set(new_items)):
            old_item, new_item = old_items[item_id], new_items[item_id]
            for field in sorted(set(old_item) | set(new_item)):
                if field == "id":
                    continue
                old_value, new_value = old_item.get(field), new_item.get(field)
                if old_value != new_value:
                    changes.append(
                        _change(
                            source_id,
                            "field_changed",
                            f"{new_item.get('title', item_id)}: '{field}' changed",
                            previous_at,
                            current_at,
                            item_id=item_id,
                            field=field,
                            previous_value=old_value,
                            current_value=new_value,
                        )
                    )
    return changes


def _change(source_id: str, change_type: str, title: str, previous_at: str, current_at: str, **extra) -> dict:
    change = {
        "source_id": source_id,
        "change_type": change_type,
        "title": title,
        "previous_observed_at": previous_at,
        "current_observed_at": current_at,
        "evidence": "observed_diff",
    }
    change.update(extra)
    return change
