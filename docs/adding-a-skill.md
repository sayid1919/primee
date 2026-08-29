# Adding a new Primee skill

A skill is a folder with two files. Keep it small and single-purpose: if it
needs two paragraphs to describe, it is probably two skills.

## 1. Create the folder

```
src/primee/skills/notes/
    SKILL.md
    handler.py
```

The folder name should match the skill's `name`.

## 2. Write `SKILL.md`

```markdown
---
name: notes
version: 1.0.0
description: "Append a quick note to today's note file in the Vault."
triggers:
  - "note"
  - "quick note"
  - "یادداشت سریع"
exclusions:
  - "daily plan"
  - "morning brief"
required_permissions:
  - vault.read
  - vault.append
inputs:
  - name: text
    type: string
    required: true
    description: "The note text."
outputs:
  - name: path
    type: string
persistence:
  vault_writes: true
  path_prefix: notes
  description: "Appends to notes/<ISO-date>.md."
handler: handler.py:run
---

# Notes

## What this skill does
...

## When it should activate
...

## When it must not activate
...

## What information it requires
...

## What it may read
...

## What it may write
...

## What requires user approval
...

## What it returns
...
```

All ten metadata fields are required. See
[docs/skill-contract.md](skill-contract.md) for the exact rules.

Two consistency rules bite immediately: `persistence.vault_writes: true`
requires a `vault.create`/`append`/`update` permission, and declaring one of
those requires `vault_writes: true`.

## 3. Write the handler

```python
from primee.core.context import SkillContext
from primee.core.errors import ErrorCode
from primee.core.result_types import SkillResult, VaultWrite

SKILL_NAME = "notes"


def run(context: SkillContext) -> SkillResult:
    text = str(context.input("text", "")).strip()
    if not text:
        return SkillResult.fail(
            SKILL_NAME,
            "There was no note text to save.",
            ErrorCode.INVALID_INPUT,
            "Pass the note with --input text=...",
        )

    path = f"notes/{context.today()}.md"
    return SkillResult.ok(
        SKILL_NAME,
        f"Prepared a note for {path}.",
        structured_data={"path": path, "characters": len(text)},
        proposed_vault_writes=[
            VaultWrite(
                path=path,
                operation="append",
                content=f"- {context.now_iso()} — {text}\n",
                reason="Append the note the user dictated.",
            )
        ],
    )
```

## Rules a handler must follow

- **Return a `SkillResult`.** Anything else is rejected as `RESULT_INVALID`.
- **Never write.** Return `proposed_vault_writes`; Primee Core decides and the
  Vault skill performs.
- **Never perform an external action.** Return `proposed_external_actions`.
  Step One records them as pending and executes none.
- **Never import `os`, `open`, `subprocess`, or a networking module.** A handler
  that needs data should get it through `context.connectors`.
- **Never invent data.** When a source is missing, say so and fail with
  `CONNECTOR_NOT_CONFIGURED` or `INSUFFICIENT_INFORMATION`. A wrong number is
  worse than no number.
- **Never put a credential in `SKILL.md`,** in a summary, or in a warning.
- Handle `context.dry_run` if the skill has any effect of its own. Most do not.

## 4. Choose triggers and exclusions carefully

Triggers are the phrases that should select your skill. Exclusions veto it:
give the new skill exclusions for the neighbouring skills' territory, and add
your skill's territory to *their* exclusions if they overlap. Overlapping
triggers without exclusions make Primee ask for clarification, which is safe but
tiresome.

Check your work before writing any tests:

```powershell
python run_primee.py list-skills
python run_primee.py explain "quick note about the meeting"
```

## 5. Add tests

At minimum:

- the manifest loads (`test_skill_loader.py` already asserts this for every
  bundled skill);
- the request routes to your skill and not to a neighbour;
- the happy path returns the expected `structured_data`;
- the missing-input path fails with the right `error_code` and writes nothing;
- a dry run writes nothing.

Use `tests.support.TempVaultCase`, `make_config()` and `make_runtime()`.

```powershell
python -m unittest discover -t . -s tests
```

## 6. Skills outside the repository

`[runtime] skills_dir` points the loader at another folder. Remember that
loading a handler imports it, so only point Primee at code you have read.
