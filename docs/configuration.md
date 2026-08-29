# Configuration

## Where it lives

All configuration lives in `config/`, in TOML, read with the standard library's
`tomllib`. Three templates are committed:

| Template | Copy to | Holds |
| --- | --- | --- |
| `primee.example.toml` | `primee.local.toml` | Vault path, runtime, audit, schedule, metric series, trend sources |
| `permissions.example.toml` | `permissions.local.toml` | The permission policy |
| `connectors.example.toml` | `connectors.local.toml` | Which connector provider each kind uses |

Every `*.local.toml` file is git-ignored. That is the intended home for anything
machine-specific.

## Load order

`load_config(path)` accepts a file or a directory.

For a directory it reads, in this order, skipping what is absent:

```
primee.toml  →  primee.local.toml
permissions.toml  →  permissions.local.toml
connectors.toml  →  connectors.local.toml
```

Later files override earlier ones, key by key. `*.example.toml` files are
**not** loaded — they are templates. A fresh clone therefore loads no
configuration at all and starts from the safe built-in defaults: no Vault, no
connectors, and every permission denied.

The CLI uses `./config` when it exists; `--config <path>` overrides that.

## Path expansion

Path settings accept `~` and `${env:VARIABLE_NAME}`:

```toml
root = "${env:USERPROFILE}/Documents/PrimeeVault"
```

An undefined variable expands to an empty string. This mechanism names an
environment variable; it never stores its value.

## Credentials

No configuration file ever contains a credential. `credential_env` names an
environment variable that a *future* connector would read at runtime:

```toml
[connectors.metrics]
provider = "none"
credential_env = "PRIMEE_METRICS_TOKEN"   # a NAME, never a value
```

Step One reads no credential at all, because it ships no real provider.

## Sections

### `[vault]`
`root` (absolute path, required for any persistence), `max_file_bytes`.

If `root` is empty, Primee falls back to the **`PRIMEE_VAULT_PATH`** environment
variable. That is the recommended setup: the path lives in your environment
rather than in a file, so no user name or personal path is ever written down in
the project.

```powershell
setx PRIMEE_VAULT_PATH "%USERPROFILE%\Documents\PrimeeVault"
```

`primee doctor` reports whether the path came from configuration or from the
environment. An explicit `root` always wins.

### `[runtime]`
`dry_run`, `skills_dir` (empty = bundled skills), `state_dir` (empty = OS
default), `min_route_score`, `ambiguity_margin`.

### `[audit]`
`enabled`, `log_message_bodies` (false, and no code path logs bodies), `path`
(empty = `<state_dir>/audit/audit.jsonl`).

### `[schedule]`
`timezone` (always `"system"`; the Windows local timezone is read at call time,
so changing it is picked up with no geographic data hardcoded), `trends_at`,
`inbox_at`, `plan_at`, `run_every_day`, `rest_days`, `missed_job_policy`.

**Step One installs no scheduler.** These values describe intent and are shown
by `doctor`. Wiring them to Windows Task Scheduler, including the missed-job
policy, is a later, separately approved step.

### `[[metrics.series]]`
One table per numeric series Primee is allowed to report. A series that is not
listed is never reported.

```toml
[[metrics.series]]
key       = "website_visits"
label     = "Website visits"
unit      = "visits"
summaries = ["latest", "previous", "delta", "average"]
```

Supported summaries: `latest`, `previous`, `sum`, `average`, `minimum`,
`maximum`, `delta`. An unsupported name is skipped with a warning rather than
being silently ignored.

### `[trends]`
`sources` — opaque identifiers a connector resolves. Primee never discovers or
invents sources.

### `[connectors.<kind>]`
`provider` is `"none"` (default, honest) or `"mock"` (reads a synthetic JSON
fixture named by `fixture`). An unrecognised provider name is treated as `none`
rather than as an error, because guessing would be worse.

## The Vault is not configuration

Your Vault holds notes, knowledge and outputs — not settings. It lives outside
this repository, and `.gitignore` blocks the obvious accidents. See
[memory.md](memory.md#why-your-real-vault-is-not-in-git).

## State directory

Runtime state — the audit log above all — lives outside both the repository and
the Vault. The default is `%LOCALAPPDATA%\Primee` on Windows,
`$XDG_STATE_HOME/primee` or `~/.local/state/primee` elsewhere.

## Checking your configuration

```
python run_primee.py doctor
```

It prints which files were loaded, whether the Vault is reachable, the connector
providers, the effective permission policy and the audit log path.
