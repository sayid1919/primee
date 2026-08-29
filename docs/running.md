# Running Primee locally

## Requirements

Python 3.11 or newer. Nothing else — Primee has zero third-party dependencies.

```powershell
python --version
```

## Two ways to run

**From a checkout, no install:**

```powershell
python run_primee.py doctor
```

**As an installed package:**

```powershell
python -m pip install -e .
python -m primee doctor
primee doctor
```

Both are the same program. `run_primee.py` just puts `src/` on `sys.path` first.

## First-time setup

```powershell
copy config\primee.example.toml       config\primee.local.toml
copy config\permissions.example.toml  config\permissions.local.toml
copy config\connectors.example.toml   config\connectors.local.toml

mkdir "$env:USERPROFILE\Documents\PrimeeVault"
```

Then set the Vault path in `config\primee.local.toml`:

```toml
[vault]
root = "${env:USERPROFILE}/Documents/PrimeeVault"
```

Forward slashes work fine on Windows. Verify with `python run_primee.py doctor`
— the `vault` line should read `ready`.

## Commands

| Command | Purpose |
| --- | --- |
| `doctor` | configuration, Vault state, connectors, permission policy |
| `list-skills` | every discovered skill, its triggers, exclusions, permissions |
| `permissions` | the permission catalogue, implemented vs reserved |
| `explain "<request>"` | how a request would route — runs nothing |
| `run "<request>"` | route and execute |
| `vault <operation>` | run one Vault memory operation through the permission layer |

Global flags work before or after the subcommand: `--config <path>` and
`--json`.

`run` also takes:

| Flag | Effect |
| --- | --- |
| `--skill <name>` | bypass routing and call that skill directly |
| `--input KEY=VALUE` | a handler input, repeatable; values are parsed as JSON when possible |
| `--dry-run` | plan every side effect, perform none |
| `--approve <permission>` | pre-approve one permission for this run only, repeatable |
| `--no-audit` | keep this run out of the audit log file |

`vault` takes the operation name plus whatever it needs: `--path`, `--target`,
`--root`, `--query`, `--tag`, `--field`, `--value`, `--limit`, `--prefix`,
`--adopt`, `--dry-run`, `--approve` and `--no-audit`.

```powershell
python run_primee.py vault init --root "<absolute path>" --dry-run
python run_primee.py vault validate
python run_primee.py vault validate_links
python run_primee.py vault rebuild_index
python run_primee.py vault search_text --query "lighthouse"
python run_primee.py vault backlinks --target wiki/lighthouse-project
```

See [memory.md](memory.md) for the full operation catalogue.

## Examples

```powershell
# See why a request routes where it does.
python run_primee.py explain "چه چیزی تغییر کرده"

# Read-only morning brief (needs a configured email or calendar connector).
python run_primee.py run "morning brief"

# Plan the day from candidates, without writing anything.
python run_primee.py run "daily plan" --dry-run `
  --input 'candidates=[{"title":"Renew the domain","reason":"it expires Friday","expected_outcome":"renewed for a year","completion_condition":"registrar shows the new expiry"}]'

# Replace today's plan. vault.update is "approval", so it must be approved.
python run_primee.py run "daily plan" --input overwrite=true --approve vault.update

# Machine-readable output for scripting.
python run_primee.py run "metrics report" --json
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | the skill ran and succeeded |
| `1` | Primee needs clarification, or the skill failed |
| `2` | bad arguments or bad configuration |

## Trying it with synthetic data

The repository ships fixtures under `tests/fixtures/data/`. Point a connector at
one to see a skill work end to end, with no real account involved:

```toml
[connectors.email]
provider = "mock"
fixture  = "tests/fixtures/data/email.json"
```

## Where things are written

- Vault content → your configured Vault root, only via the Vault skill.
  Skill outputs land in `outputs/` with an ISO-dated filename.
- Audit log → `<state_dir>/audit/audit.jsonl`, outside the repo and the Vault.
- Nothing else. Primee writes nowhere else and sends nothing anywhere.
