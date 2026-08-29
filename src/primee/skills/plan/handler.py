"""Plan skill handler.

The plan is assembled from candidates the user already approved.  If there are
none, the skill says so and returns nothing: an invented priority is worse than
no plan.  Writes go through Primee Core to the Vault skill, never directly.
"""

from __future__ import annotations

from typing import Any

from primee.core.context import SkillContext
from primee.core.errors import ErrorCode
from primee.core.result_types import SkillResult, VaultWrite

SKILL_NAME = "plan"
MAX_PRIORITIES = 3
DEFAULT_CANDIDATES_PAGE = "wiki/plan-candidates"

REQUIRED_FIELDS = ("title", "reason", "expected_outcome", "completion_condition")


def run(context: SkillContext) -> SkillResult:
    day = str(context.input("day", "") or context.today()).strip()
    if not _is_iso_date(day):
        return SkillResult.fail(
            SKILL_NAME,
            "The requested date is not a valid ISO date.",
            ErrorCode.INVALID_INPUT,
            "'day' must look like 2026-08-27.",
        )

    candidates, source, warnings = _collect_candidates(context)
    valid, rejected = _validate(candidates)

    for entry in rejected:
        warnings.append(
            f"Candidate {entry['index']} was ignored: missing "
            + ", ".join(entry["missing"])
            + "."
        )

    structured: dict[str, Any] = {
        "day": day,
        "generated_at": context.now_iso(),
        "candidate_source": source,
        "candidate_count": len(candidates),
        "usable_candidate_count": len(valid),
        "priorities": [],
    }

    if not valid:
        return SkillResult.fail(
            SKILL_NAME,
            f"There is not enough approved information to plan {day}.",
            ErrorCode.INSUFFICIENT_INFORMATION,
            "Primee needs candidate actions that each carry a title, a reason, an "
            "expected outcome and a completion condition. It will not invent "
            f"priorities. Add them to '{_candidates_page(context)}' in your Vault "
            "or pass them as the 'candidates' input.",
            structured_data=structured,
            warnings=warnings,
        )

    priorities = valid[:MAX_PRIORITIES]
    structured["priorities"] = priorities
    if len(valid) < MAX_PRIORITIES:
        warnings.append(
            f"Only {len(valid)} usable candidate(s) were available, so the plan "
            f"has {len(valid)} priorit{'y' if len(valid) == 1 else 'ies'} instead of "
            f"{MAX_PRIORITIES}. Primee did not pad the list."
        )

    document = _render(day, priorities, context.now_iso(), source)

    lines = [f"Top {len(priorities)} priorit{'y' if len(priorities) == 1 else 'ies'} for {day}:"]
    for index, priority in enumerate(priorities, start=1):
        lines.append(f"{index}. {priority['title']}")
        lines.append(f"   why: {priority['reason']}")
        lines.append(f"   outcome: {priority['expected_outcome']}")
        lines.append(f"   done when: {priority['completion_condition']}")

    return SkillResult.ok(
        SKILL_NAME,
        "\n".join(lines),
        structured_data=structured,
        warnings=warnings,
        proposed_vault_writes=[
            VaultWrite(
                path="",  # the Vault generates the ISO-dated filename
                operation="publish_output",
                content=document,
                reason=f"Persist the daily plan for {day}.",
                metadata={
                    "title": f"Daily plan {day}",
                    "summary": (
                        f"{len(priorities)} priorit"
                        f"{'y' if len(priorities) == 1 else 'ies'} for {day}."
                    ),
                    "output_type": "output_plan",
                    "tags": ["plan", f"day/{day}"],
                    "status": "final",
                    "source": "primee plan skill",
                    # File the plan under the day it is for, not the moment it
                    # was generated. Frontmatter still records the real time.
                    "date_hint": day,
                    "related": [source] if source not in ("input", "none") else [],
                },
            )
        ],
    )


def _candidates_page(context: SkillContext) -> str:
    page = str(context.input("candidates_page", "") or DEFAULT_CANDIDATES_PAGE).strip()
    return page[:-3] if page.endswith(".md") else page


def _collect_candidates(context: SkillContext) -> tuple[list[dict], str, list[str]]:
    warnings: list[str] = []
    supplied = context.input("candidates", None)
    if isinstance(supplied, list) and supplied:
        return [item for item in supplied if isinstance(item, dict)], "input", warnings

    page = _candidates_page(context)
    read = context.vault.read(f"{page}.md")
    if read.ok:
        parsed = _parse_candidates_markdown(read.body or read.content or "")
        if parsed:
            return parsed, page, warnings
        warnings.append(
            f"'{page}' was read but contained no candidate entries in the "
            "expected '## title' format."
        )
        return [], page, warnings
    if read.error_code and read.error_code != ErrorCode.VAULT_FILE_MISSING:
        warnings.append(f"'{page}' could not be read ({read.error_code}).")
    return [], "none", warnings


def _parse_candidates_markdown(text: str) -> list[dict]:
    """Parse ``## title`` blocks with ``- key: value`` lines beneath them."""
    candidates: list[dict] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            if current:
                candidates.append(current)
            current = {"title": line[3:].strip()}
            continue
        if current is not None and line.startswith("- ") and ":" in line:
            key, _, value = line[2:].partition(":")
            key = key.strip().lower().replace(" ", "_")
            if key in REQUIRED_FIELDS:
                current[key] = value.strip()
    if current:
        candidates.append(current)
    return candidates


def _validate(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    valid: list[dict] = []
    rejected: list[dict] = []
    for index, candidate in enumerate(candidates, start=1):
        missing = [
            field
            for field in REQUIRED_FIELDS
            if not str(candidate.get(field, "")).strip()
        ]
        if missing:
            rejected.append({"index": index, "missing": missing})
            continue
        valid.append(
            {
                "title": str(candidate["title"]).strip(),
                "reason": str(candidate["reason"]).strip(),
                "expected_outcome": str(candidate["expected_outcome"]).strip(),
                "completion_condition": str(candidate["completion_condition"]).strip(),
                "source": str(candidate.get("source", "approved_candidate")).strip(),
            }
        )
    return valid, rejected


def _render(day: str, priorities: list[dict], generated_at: str, source: str) -> str:
    """The Markdown body. Primee Memory writes the frontmatter itself."""
    lines = [
        f"Generated at {generated_at} from candidates in `{source}`.",
        "",
    ]
    for index, priority in enumerate(priorities, start=1):
        lines.extend(
            [
                f"## {index}. {priority['title']}",
                "",
                f"- Why today: {priority['reason']}",
                f"- Expected outcome: {priority['expected_outcome']}",
                f"- Done when: {priority['completion_condition']}",
                "",
            ]
        )
    lines.extend(
        [
            "---",
            "",
            "Generated by Primee from approved candidates. Priorities are never "
            "invented: if there were fewer than three usable candidates, this plan "
            "is shorter on purpose.",
            "",
        ]
    )
    return "\n".join(lines)


def _is_iso_date(value: str) -> bool:
    from datetime import date

    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True
