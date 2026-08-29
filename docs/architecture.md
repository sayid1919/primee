# Architecture

## The one path a request can take

```
request text
   │
   ▼
[1] Router            deterministic scoring over triggers and exclusions
   │                  -> matched | ambiguous | no_match | unknown_command
   ▼
[2] Declaration       does the skill's SKILL.md list this permission?
   │
   ▼
[3] Policy            does the user's permission policy allow it?  (deny by default)
   │
   ▼
[4] Approval gate     for policy mode "approval", was it approved for this run?
   │
   ▼
[5] Handler           runs with data + a Vault READ proxy. No filesystem, no network.
   │
   ▼
[6] Result validation the structured result contract, enforced, with redaction
   │
   ▼
[7] Vault skill       performs each approved proposed write (or dry-run skips it)
   │
   ▼
[8] Audit             one sanitized event per decision, appended to a JSONL log
```

Steps 2, 3, 4, 6, 7 and 8 live in `primee.core.runtime`. A handler cannot skip
any of them, because a handler never receives a filesystem handle, a network
socket, or a writable Vault: it returns *proposals*, and Primee Core decides.

## Modules

| Module | Responsibility |
| --- | --- |
| `core/frontmatter.py` | Strict YAML-subset parser for untrusted `SKILL.md` |
| `core/manifest.py` | Validates metadata into an immutable `SkillManifest` |
| `core/skill_loader.py` | Discovery, duplicate rejection, safe lazy handler binding |
| `core/router.py` | `Router` interface + the Step One `DeterministicRouter` |
| `core/permissions.py` | Permission catalogue and the deny-by-default engine |
| `core/approval.py` | Approval gates (deny-all, pre-approved, callback) |
| `core/result_types.py` | `SkillResult`, `VaultWrite`, `ExternalAction` + validation |
| `core/paths.py` | Path shape and path resolution safety |
| `core/redaction.py` | Secret and personal-data redaction |
| `core/audit.py` | Sanitized append-only audit events |
| `core/config.py` | TOML configuration loading (stdlib `tomllib`) |
| `core/clock.py` | Injectable local-time clock |
| `core/context.py` | The read-only `SkillContext` handed to handlers |
| `core/runtime.py` | Orchestration, guarded connectors, Vault plumbing |
| `connectors/` | Read-only connector interfaces, mocks, not-configured defaults |
| `skills/` | The five bundled skills, each a folder with `SKILL.md` + `handler.py` |

## Designed to be replaced

Three seams exist specifically so later steps do not require a rewrite:

- **`Router`** is an abstract class. A future native Primee intelligence engine
  implements `route(request, registry) -> RouteDecision` and is passed to
  `PrimeeRuntime(router=...)`. Nothing else changes.
- **Connectors** are abstract read-only interfaces. Replacing a mock with a real
  provider is a new class plus a config line, not a change to any skill.
- **`ApprovalGate`** is an abstract class. A future HUD or voice prompt
  implements `request(ApprovalRequest) -> ApprovalOutcome`.

## Why a handler cannot cheat

- It receives `SkillContext`, which exposes data, a clock, permission-guarded
  connectors and a Vault *read* proxy. No storage object, no `open()` helper.
- The Vault read proxy is a full round trip through Primee Core, so reads are
  permission-checked and audited exactly like writes.
- `context.storage` is populated only for the Vault skill itself, and only by
  Primee Core.
- Its return value is re-validated, and its `summary`, `warnings` and error
  message are re-redacted, whatever the handler did.
- If it raises, Primee Core replaces the exception with a generic sanitized
  failure so a traceback cannot leak data.
