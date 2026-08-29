# Testing

```powershell
python -m unittest discover -t . -s tests
```

Verbose, or a single module:

```powershell
python -m unittest discover -t . -s tests -v
python -m unittest tests.test_vault_paths
python -m unittest tests.test_router.AmbiguityTests.test_two_equally_good_matches_are_ambiguous
```

The suite uses the standard library's `unittest`. There is no `pytest`
dependency and no plugin to install.

## Ground rules

Every test uses synthetic data. No test contacts a real account, mailbox,
calendar or website; none reads a credential or a private file; none opens a
network connection. Fixtures live in `tests/fixtures/` and every value in them is
invented.

## What is covered

| Module | Covers |
| --- | --- |
| `test_frontmatter.py` | valid parsing; rejection of malformed YAML, tags, anchors, flow style, tabs, duplicate keys, control and bidi characters; parser inertness |
| `test_skill_loader.py` | discovery, every required field, duplicate skill-name rejection, handler path escape, lazy binding |
| `test_router.py` | trigger matching (English and Persian), explicit commands, exclusions, ambiguity, no-match, determinism |
| `test_permissions.py` | deny by default, undeclared permissions, reserved capabilities, all three approval gates |
| `test_result_types.py` | the full result contract and enforced redaction |
| `test_vault_paths.py` | traversal, absolute paths, drive letters, reserved device names, symlink and junction escape, listing containment |
| `test_vault_operations.py` | create/read/append/update, absence of delete, credential refusal, atomicity |
| `test_redaction.py` | credentials, tokens, emails, digit runs, account names; ordinary text untouched |
| `test_audit.py` | required fields, redacted details, append-only JSONL |
| `test_connectors.py` | not-configured defaults, read-only surface, mock fixtures, error handling |
| `test_runtime.py` | routing integration, permission denial, approval, dry run, Vault gateway, handler failure, external actions |
| `test_skills.py` | the behaviour of all five bundled skills |
| `test_config.py` | defaults, validation, expansion, the shipped templates, credential hygiene |
| `test_independence.py` | no AI SDK, no third-party import, no networking, no shell execution, no Claude directory |
| `test_cli.py` | every command, exit codes, JSON output, dry run, `--approve` |

## Platform-dependent results

Symbolic-link tests report **skipped**, not passed, where the platform or
account cannot create links — Windows without Developer Mode or elevation, for
example. `python -m unittest discover -t . -s tests -v` shows which ones skipped.
Read the skip list; a skipped security test is not a passing one.

## Adding a test

Put it in the module that owns the behaviour. If it needs a real directory,
inherit from `tests.support.TempVaultCase`, which gives you an isolated
temporary Vault plus an "outside" directory for escape attempts. Use
`make_config()` and `make_runtime()` from the same module to build a runtime with
a fixed clock and an in-memory audit sink.
