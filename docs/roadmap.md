# Roadmap — what is real, what is a placeholder

Step One is a foundation. This page is the honest inventory: what works today,
what is deliberately a stub, and which later step is meant to fill each gap.

## Fully working today

- Skill discovery from the filesystem, with safe `SKILL.md` parsing.
- Manifest validation, including rejection of malformed frontmatter, missing
  fields, unknown permissions, duplicate skill names and handler path escapes.
- The deterministic router: explicit commands, trigger scoring, exclusions,
  ambiguity detection, refusal to guess.
- The deny-by-default permission engine and the three approval gates.
- Global dry-run mode.
- The structured result contract, with validation and enforced redaction.
- **The Vault, completely.** Read, list, create, append and update, each
  separately permissioned, with double path validation, symlink and junction
  refusal, safe UTF-8 filenames, atomic writes, credential refusal and
  requesting-skill attribution.
- The sanitized append-only audit trail.
- The local CLI and 272 automated tests.
- `plan`: three priorities with reason, expected outcome and completion
  condition, written to `plans/<ISO-date>.md` through the Vault.
- `trends`: real snapshot comparison against the previous Vault snapshot, with
  observation separated from interpretation.

## Placeholders, and why

| Component | State in Step One | Filled by |
| --- | --- | --- |
| Metrics connector | Interface + not-configured default + fixture mock | A metrics integration step, after you choose a platform |
| Email connector | Interface + mock, read-only, metadata only | An email integration step |
| Calendar connector | Interface + mock, read-only | A calendar integration step |
| Trends connector | Interface + fixture mock, no network at all | A sources step, after you list URLs and feeds |
| Credential storage | `credential_env` names a variable; nothing is read | The first real connector step |
| `interpretation` in trends | Always empty, by design | The native Primee intelligence engine |
| Router | Deterministic scoring | The same engine, via the `Router` interface |
| Approval prompt | `--approve` blanket grant per run | An interactive prompt or HUD step |

## Not built at all in Step One

| Capability | Note |
| --- | --- |
| Voice command and local speech output | Permission names are reserved; no code exists |
| File search and management outside the Vault | `fs.read` / `fs.write` reserved and unconditionally denied |
| Operating system actions | `system.execute` reserved and unconditionally denied |
| Scheduling at 07:30 / 08:00 | `[schedule]` is read by `doctor` and nothing else; no scheduler is installed |
| Missed-job handling | Designed (`propose_once`), not implemented |
| HUD / Node.js user interface | Not started |
| Multi-device sync | Deliberately excluded; the architecture does not block it |
| Encryption at rest | Not present; use BitLocker or an encrypted volume |

## Reserved permissions

These can be declared by a skill and listed in a policy, but Primee Core refuses
them unconditionally because no implementation exists:

`fs.read`, `fs.write`, `email.send`, `calendar.write`, `network.fetch`,
`system.execute`, `audio.capture`, `camera.capture`.

Attempting one produces `PERMISSION_DENIED` with the reason "reserved for a later
Primee step and is not implemented", and an audit event.

## Suggested order for later steps

1. **Scheduling** — Windows Task Scheduler, the missed-job policy, a
   `primee scheduled-run` entry point. No new external surface.
2. **One real connector** — probably metrics, since it is read-only and low
   risk. Establishes the credential-store pattern for everything after it.
3. **Email and calendar, read-only** — the morning brief becomes real. Sending
   and calendar writes stay denied.
4. **Trends sources** — real feeds behind the existing connector interface, with
   an allowlist and a fetch budget.
5. **Interactive approval** — a per-action prompt replacing the blanket
   `--approve`.
6. **The native intelligence engine** — a local model behind the `Router`
   interface, filling the `interpretation` list that is empty today.
7. **Voice**, then **file and system actions**, each with its own permission
   surface and its own approval design.

Nothing beyond Step One begins without explicit approval.
