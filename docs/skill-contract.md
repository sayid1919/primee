# The Primee skill contract

A skill is a directory containing exactly one `SKILL.md` and one handler file.
Skills are small and single-purpose.

```
src/primee/skills/<name>/
    SKILL.md      metadata (YAML frontmatter) + documentation (Markdown body)
    handler.py    the code, exporting the function named in `handler`
```

## `SKILL.md` is untrusted configuration

It is parsed by a purpose-built strict YAML-subset parser, not by a general YAML
library. The parser supports block mappings, block sequences, quoted and plain
scalars, integers, floats, booleans, null, `[]`, `{}` and `#` comments.

It **rejects** tags (`!!python/...`), anchors, aliases, merge keys, flow
collections, block scalars, tab indentation, duplicate keys, control characters
and bidirectional override characters. It returns only plain `str`, `int`,
`float`, `bool`, `None`, `list` and `dict` values. Nothing in a `SKILL.md` file
is ever executed, evaluated or passed to a shell.

## Required metadata

| Field | Type | Rule |
| --- | --- | --- |
| `name` | string | `^[a-z][a-z0-9_]{0,31}$`, unique across all skills |
| `version` | string | `MAJOR.MINOR.PATCH` |
| `description` | string | non-empty, at most 500 characters |
| `triggers` | list of strings | at least one, no duplicates |
| `exclusions` | list of strings | may be empty (`[]`) |
| `required_permissions` | list of strings | each must be a known, skill-requestable permission |
| `inputs` | list of `{name, type, required?, description?}` | `type` from the allowed set |
| `outputs` | list of `{name, type, description?}` | same rules |
| `persistence` | mapping | `vault_writes` (bool), optional `path_prefix`, `description` |
| `handler` | string | `<file>.py:<function>`, the file must be inside the skill directory |

Two consistency rules are enforced at load time:

- `persistence.vault_writes: true` requires at least one of `vault.create`,
  `vault.append`, `vault.update` in `required_permissions`;
- declaring one of those permissions requires `persistence.vault_writes: true`.

Extra keys are preserved in `manifest.extra` and ignored.

Allowed I/O types: `string`, `integer`, `number`, `boolean`, `date`, `list`,
`object`.

## The Markdown body

The body must explain, in this order:

1. what the skill does,
2. when it should activate,
3. when it must not activate,
4. what information it requires,
5. what it may read,
6. what it may write,
7. what requires user approval,
8. what it returns.

The body is documentation for a human. Primee never parses it for behaviour.

## Handler binding

`handler: handler.py:run` is resolved to a file **inside the skill's own
directory**. Primee refuses a handler that is a symbolic link, or whose real
path lands outside that directory, so a `SKILL.md` can never point Primee at
arbitrary code elsewhere on the machine.

The handler is imported **lazily**, only when the skill actually runs.
`primee list-skills` and `primee explain` import nothing.

> **Known limitation.** Importing a handler executes its module-level code. The
> bundled skills are part of this repository and are reviewed like any other
> source file. If you ever install a skill you did not write, read its handler
> first: Primee sandboxes the *metadata*, not the Python file it names.

## The handler signature

```python
from primee.core.context import SkillContext
from primee.core.result_types import SkillResult


def run(context: SkillContext) -> SkillResult:
    ...
```

`SkillContext` gives you `skill_name`, `request`, `inputs`, `config`, `clock`,
`connectors` (permission-guarded, read-only), `vault` (read proxy), `dry_run`,
and the helpers `today()`, `now_iso()` and `input(name, default)`.

## The standard result

Every handler returns a `SkillResult` with exactly these fields:

| Field | Meaning |
| --- | --- |
| `success` | did the skill do its job |
| `skill_name` | the skill's own name |
| `summary` | short human-readable text, redacted on validation |
| `structured_data` | JSON-serialisable machine-readable output |
| `proposed_vault_writes` | list of `VaultWrite`, **proposals only** |
| `proposed_external_actions` | list of `ExternalAction`, **never executed in Step One** |
| `warnings` | non-fatal notes, redacted on validation |
| `error_code` | a stable code from `ErrorCode`, required when `success` is false |
| `sanitized_error_message` | user-safe text, required when `success` is false, redacted |

Validation rejects a successful result carrying an error code, a failed result
without one, an unknown error code, a non-serialisable `structured_data`, and a
raw dict where a `VaultWrite` was expected.
