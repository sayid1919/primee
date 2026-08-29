# Security decisions, and their limits

This document states what Primee Step One actually does, and where it stops. It
does not claim more than the code delivers.

## Decisions

| Decision | How it is enforced |
| --- | --- |
| Deny by default | Unlisted permissions fall back to `never`; a fresh checkout can do nothing |
| Declared permissions | A skill can only use what its own `SKILL.md` lists |
| Skills cannot widen their permissions | Manifest is data; the policy lives in user-owned config |
| Connectors are read-only | The interfaces expose no write method; tests assert this |
| Connectors are permission-gated | Skills get a guarded view, not the registry |
| External side effects need approval | Step One executes **none**; they are recorded as pending |
| Dry-run mode | `--dry-run` blocks every write, at two independent layers |
| No hardcoded secrets | Config may name an environment variable, never a value |
| No credentials in examples, tests, logs, docs or the Vault | Enforced by tests and by Vault content refusal |
| Local secret files are git-ignored | `.env*`, `*.local.toml`, `*.pem`, `*.key`, `secrets/` |
| Redaction of errors and audit events | Every string passes `redact_text`; sensitive keys are dropped whole |
| Path validation before access | Shape check, then resolution check, on every Vault path |
| Full audit trail | Timestamp, skill, action, permission, approval state, sanitized outcome |
| Message content is never logged | `log_message_bodies` defaults to false, and no code path logs bodies |
| No shell execution from metadata | `SKILL.md` is parsed by an inert parser; no `subprocess`, no `eval`, no `exec` anywhere |
| No data transmission | No networking module is imported anywhere in `src/` |

`tests/test_independence.py` parses every source file and asserts the last three
rows mechanically, so a regression fails the suite rather than going unnoticed.

## Redaction

Redaction is aggressive and prefers over-redacting. It removes private key
blocks, bearer tokens, basic-auth credentials in URLs, `key = value` credential
assignments (including compound names such as `refresh_token` and `x-api-key`),
email addresses, digit runs of 12–24 characters, the account name inside
`C:\Users\…` and `/home/…`, and high-entropy blobs of 40+ characters. Values
under a sensitive key are removed regardless of what they look like.

The primary defence is that Primee never *collects* a credential. Redaction is
the second layer, not the first.

## Honest limits

These are real gaps, stated plainly rather than papered over.

1. **Importing a handler runs its module-level code.** Primee proves the handler
   file lives inside the skill directory and is not a symlink, and it imports
   lazily so listing skills executes nothing. It does not sandbox the Python
   file itself. Read any skill you did not write.
2. **Redaction is heuristic.** A credential in an unusual format can slip
   through. Do not put credentials anywhere near Primee.
3. **The audit log is append-only by convention, not by enforcement.** Anyone
   with write access to the file can edit it. There is no signing or chaining.
4. **File permissions are best-effort.** The audit file is created with mode
   `0o600`, which POSIX honours and Windows largely ignores. Protect the folder
   with Windows ACLs if that matters to you.
5. **Symbolic-link tests skip where links cannot be created.** On Windows
   without Developer Mode or elevation, the symlink tests report `skipped`, not
   `passed`. The junction case is still covered by the `realpath` containment
   check. Verify on your own machine before relying on it.
6. **No encryption at rest.** The Vault is plain Markdown. Use BitLocker or an
   encrypted volume if you need that.
7. **No multi-user model.** Primee assumes one trusted user on one machine.
8. **`primee run --approve X` is a blanket grant for that run.** It approves
   every request for permission `X` in that single invocation, not one specific
   file. An interactive per-action prompt is a later step.
9. **The `[schedule]` section is inert.** Primee installs no scheduler and runs
   nothing on a timer. Those values are read by `doctor` and nothing else.

## Reporting

This is a personal project with a single user. If you find a problem, fix it on
a branch and add a regression test to `tests/`.
