# Primee — Step One

Primee is a local-first personal operating system. This repository contains
**Step One**: the skill foundation. It runs on a plain Python installation, on
one Windows machine, with no network access and no hosted AI service.

## Independence

Claude Code was used as a temporary tool to help write this source code. It is
**not** a runtime dependency, and neither is any hosted AI service:

- no `anthropic`, `openai`, `google-generativeai`, `cohere` or `mistralai` SDK
- no Claude, ChatGPT or Gemini API call, at any point
- no vendor agent runtime
- no `~/.claude/` directory, and no Claude-specific files anywhere in the repo
- **zero third-party Python packages at all** — runtime *and* tests

The Step One router is deterministic: explicit commands, trigger phrases,
exclusions and a transparent score. There is no model in the loop.
`tests/test_independence.py` enforces all of the above by parsing every source
file, and `primee doctor` reports `hosted_ai_dependencies: []`.

## What Step One does

1. Discovers project-local skills from the filesystem.
2. Reads and validates their `SKILL.md` metadata safely.
3. Identifies which skill matches a request.
4. Detects ambiguous requests and refuses to guess.
5. Checks required permissions, deny-by-default.
6. Runs an approved skill handler.
7. Returns a standard, validated, structured result.
8. Sends approved persistent output through the Vault skill.
9. Records a sanitized audit event for everything.
10. Runs locally, offline, without Claude Code.

## What Step One does **not** do yet

Voice input and output, file management outside the Vault, operating system
actions, scheduling, a HUD, multi-device sync, and any real email, calendar,
metrics or web connector. See [`docs/roadmap.md`](docs/roadmap.md) for the full,
honest list of placeholders.

## Requirements

- Python 3.11 or newer (3.12 recommended). Nothing else.
- Windows 10/11 64-bit is the target; the code is plain, portable Python and the
  test suite also runs on Linux and macOS.

## Quick start

```powershell
# 1. From the repository root, check what Primee sees.
python run_primee.py doctor

# 2. Create your own configuration from the templates.
copy config\primee.example.toml       config\primee.local.toml
copy config\permissions.example.toml  config\permissions.local.toml
copy config\connectors.example.toml   config\connectors.local.toml

# 3. Edit config\primee.local.toml and set your Vault path. It must be a folder
#    OUTSIDE this repository, for example:
#      root = "${env:USERPROFILE}/Documents/PrimeeVault"
#    Create that folder before running Primee.

# 4. Ask Primee something.
python run_primee.py explain "what changed this week?"
python run_primee.py run "morning brief"
python run_primee.py run "daily plan" --dry-run
```

`python -m primee ...` works identically after `pip install -e .`, and
`run_primee.py` needs no install step at all.

## Running the tests

```powershell
python -m unittest discover -t . -s tests
```

Every test uses synthetic data. No test touches a real account, mailbox,
calendar, website, credential or private file.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/architecture.md](docs/architecture.md) | How a request flows through Primee Core |
| [docs/skill-contract.md](docs/skill-contract.md) | The `SKILL.md` contract, field by field |
| [docs/routing.md](docs/routing.md) | The deterministic router and its scoring |
| [docs/permissions.md](docs/permissions.md) | Deny-by-default permissions and approval |
| [docs/vault.md](docs/vault.md) | Vault isolation and the write path |
| [docs/security.md](docs/security.md) | Security decisions, and their honest limits |
| [docs/configuration.md](docs/configuration.md) | Where configuration lives |
| [docs/running.md](docs/running.md) | Running Primee locally |
| [docs/testing.md](docs/testing.md) | Running and extending the test suite |
| [docs/adding-a-skill.md](docs/adding-a-skill.md) | Adding a new Primee skill |
| [docs/roadmap.md](docs/roadmap.md) | Every placeholder, and which step fills it |

## Repository layout

```
config/    configuration templates (never real paths, never credentials)
src/       the Primee package: core, skills, connectors
tests/     unittest suite plus synthetic fixtures
docs/      documentation
```

The real Vault, the audit log and your local configuration all live **outside**
this repository and are excluded by `.gitignore`.
