# Permissions

## Deny by default

Anything not explicitly allowed is refused. This is enforced in code
(`PermissionPolicy.default_mode` is `"never"` and unlisted permissions fall back
to it), not by convention. A fresh checkout with no configuration can read
nothing and write nothing.

## Two independent gates

A capability is used only if **both** agree:

1. **Declaration** — the skill's `SKILL.md` lists the permission in
   `required_permissions`. A skill can never use a capability it did not declare,
   even if the policy would allow it. Error: `PERMISSION_NOT_DECLARED`.
2. **Policy** — your `permissions.local.toml` maps that permission to `auto` or
   `approval`. Error: `PERMISSION_DENIED`.

Because the manifest is read from the skill folder and the policy from your own
configuration, a skill cannot widen its own permissions.

## Modes

| Mode | Meaning |
| --- | --- |
| `auto` | allowed without asking, once you have set it |
| `approval` | allowed only with an explicit approval for that single run |
| `never` | refused, always (the default for everything) |

## The catalogue

Implemented in Step One:

| Permission | Grants |
| --- | --- |
| `vault.read` | read one file from the Vault |
| `vault.list` | list files in the Vault |
| `vault.create` | create a new Vault file |
| `vault.append` | append to a Vault file |
| `vault.update` | replace an existing Vault file |
| `vault.init` | create the Vault folder structure at an approved path |
| `vault.index` | regenerate `INDEX.md` from the pages themselves |
| `connector.metrics.read` | read numbers from the metrics connector |
| `connector.email.read` | read message metadata (never bodies) |
| `connector.calendar.read` | read event metadata |
| `connector.trends.read` | read a trends snapshot |

Reserved for later steps. A skill may declare them and a policy may list them,
but Primee Core **refuses them unconditionally** because no implementation
exists: `fs.read`, `fs.write`, `email.send`, `calendar.write`, `network.fetch`,
`system.execute`, `audio.capture`, `camera.capture`.

`audit.write` exists but is not skill-requestable: only Primee Core writes audit
events, and a manifest that asks for it fails to load.

## Approval gates

| Gate | Behaviour |
| --- | --- |
| `DenyAllApprovalGate` | the default — refuses everything needing approval |
| `PreApprovedGate` | grants exactly the permissions named by `--approve` |
| `CallbackApprovalGate` | delegates to a function; a crashing prompt counts as a refusal |

A gate can only narrow what the policy already allowed. It can never grant a
permission the policy denies.

```powershell
# Refused: vault.update is "approval" and nothing was approved.
python run_primee.py run "daily plan" --input overwrite=true

# Allowed for this one run only.
python run_primee.py run "daily plan" --input overwrite=true --approve vault.update
```

## Dry run

`--dry-run` (or `[runtime] dry_run = true`) plans every side effect and performs
none. Vault writes are reported with state `dry_run` and error code
`DRY_RUN_BLOCKED`, the audit trail records that they were skipped, and the Vault
skill refuses the write a second time even if it is somehow reached directly.

## Connectors are gated too

Skills never touch the connector registry directly. Primee Core hands them a
`GuardedConnectors` view that checks `connector.<kind>.read` on every lookup. A
refusal yields a `DeniedConnector` that returns no data at all, and the skill
reports `PERMISSION_DENIED` rather than pretending it has no data source.

## Memory operations

Every named Vault operation maps to exactly one permission, in one shared table
(`primee/memory/operations.py`) used by both the gate and the implementation.
See [memory.md](memory.md#operations) for the full catalogue.

Two rules are enforced by the memory service on top of the permission layer,
because no permission should be able to grant them:

- A raw note can never be updated or renamed, by anyone.
- An output marked `final` or `shipped` can never be overwritten, by anyone.

Correcting either produces a new linked page instead.

## Inspecting the policy

```
python run_primee.py permissions      # the catalogue, with implemented/reserved
python run_primee.py doctor           # your effective policy, mode by mode
```
