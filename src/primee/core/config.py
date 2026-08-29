"""Configuration loading.

Primee reads TOML with the standard library ``tomllib`` module.  There are no
runtime dependencies and no hidden defaults that point at the network.

Configuration may live in a single file or in a directory containing
``primee.toml``, ``permissions.toml`` and ``connectors.toml``.  Any file whose
name ends in ``.local.toml`` is git-ignored and is the intended home for
machine specific paths.

Nothing in this module ever reads or stores a credential.  Connector
configuration may only name an *environment variable* or an operating system
credential store entry; the value is never read here and never persisted.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

from .errors import ConfigError
from .permissions import PermissionPolicy

DEFAULT_CONFIG_DIRNAME = "config"

#: Environment variable consulted when [vault] root is empty. This is how the
#: Vault path stays configurable per machine without any personal path or user
#: name ever appearing in the repository.
VAULT_PATH_ENV = "PRIMEE_VAULT_PATH"
MAIN_FILE = "primee.toml"
PERMISSIONS_FILE = "permissions.toml"
CONNECTORS_FILE = "connectors.toml"

_ENV_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]{0,63})\}")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

WEEKDAY_NAMES = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)


@dataclass(frozen=True)
class VaultConfig:
    root: Optional[str] = None
    max_file_bytes: int = 512 * 1024

    @property
    def configured(self) -> bool:
        return bool(self.root and str(self.root).strip())


@dataclass(frozen=True)
class MetricSeries:
    """One numeric series the metrics skill is allowed to summarise."""

    key: str
    label: str
    unit: str = ""
    summaries: tuple[str, ...] = ("latest",)


@dataclass(frozen=True)
class ConnectorConfig:
    kind: str
    provider: str = "none"
    options: Mapping[str, Any] = field(default_factory=dict)
    credential_env: str = ""

    @property
    def configured(self) -> bool:
        return self.provider not in ("", "none")


@dataclass(frozen=True)
class ScheduleConfig:
    timezone_mode: str = "system"
    trends_at: str = "07:30"
    inbox_at: str = "08:00"
    plan_at: str = "08:00"
    run_every_day: bool = True
    rest_days: tuple[str, ...] = ()
    missed_job_policy: str = "propose_once"


@dataclass(frozen=True)
class AuditConfig:
    enabled: bool = True
    log_message_bodies: bool = False
    path: Optional[str] = None


@dataclass(frozen=True)
class RuntimeConfig:
    dry_run: bool = False
    skills_dir: Optional[str] = None
    state_dir: Optional[str] = None
    min_route_score: float = 0.35
    ambiguity_margin: float = 0.15


@dataclass(frozen=True)
class PrimeeConfig:
    vault: VaultConfig = field(default_factory=VaultConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    permissions: PermissionPolicy = field(default_factory=PermissionPolicy)
    connectors: Mapping[str, ConnectorConfig] = field(default_factory=dict)
    metrics_series: tuple[MetricSeries, ...] = ()
    trends_sources: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()

    def connector(self, kind: str) -> ConnectorConfig:
        return self.connectors.get(kind, ConnectorConfig(kind=kind))

    def state_directory(self) -> Path:
        configured = self.runtime.state_dir
        if configured:
            return Path(expand(configured))
        return default_state_dir()

    def audit_path(self) -> Path:
        if self.audit.path:
            return Path(expand(self.audit.path))
        return self.state_directory() / "audit" / "audit.jsonl"

    def describe(self) -> dict:
        return {
            "vault_configured": self.vault.configured,
            "vault_source": (
                "configuration"
                if self.vault.configured and not os.environ.get(VAULT_PATH_ENV)
                else ("environment" if self.vault.configured else "unset")
            ),
            "dry_run": self.runtime.dry_run,
            "skills_dir": self.runtime.skills_dir or "(bundled)",
            "state_dir": str(self.state_directory()),
            "audit_enabled": self.audit.enabled,
            "log_message_bodies": self.audit.log_message_bodies,
            "connectors": {
                kind: {"provider": conf.provider, "configured": conf.configured}
                for kind, conf in sorted(self.connectors.items())
            },
            "metrics_series": [series.key for series in self.metrics_series],
            "schedule": {
                "trends_at": self.schedule.trends_at,
                "inbox_at": self.schedule.inbox_at,
                "plan_at": self.schedule.plan_at,
                "rest_days": list(self.schedule.rest_days),
            },
            "source_files": list(self.source_files),
        }


def default_state_dir() -> Path:
    """Per-user state directory, outside the repository and outside the Vault."""
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "Primee"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "primee"
    return Path.home() / ".local" / "state" / "primee"


def expand(value: str) -> str:
    """Expand ``~`` and ``${env:NAME}`` references in a path-like setting."""
    text = str(value)

    def _replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    text = _ENV_RE.sub(_replace, text)
    return os.path.expanduser(text)


def _read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Configuration file '{path.name}' is not valid TOML.") from exc
    except OSError as exc:
        raise ConfigError(f"Configuration file '{path.name}' could not be read.") from exc


def load_config(source: Optional[Path | str] = None) -> PrimeeConfig:
    """Load configuration from a file, a directory, or built-in defaults."""
    data: dict[str, Any] = {}
    used: list[str] = []

    if source is None:
        return PrimeeConfig(connectors=_default_connectors())

    path = Path(expand(str(source)))
    if path.is_dir():
        for name in (MAIN_FILE, PERMISSIONS_FILE, CONNECTORS_FILE):
            candidate = path / name
            local = path / name.replace(".toml", ".local.toml")
            for chosen in (candidate, local):
                if chosen.is_file():
                    _merge(data, _read_toml(chosen))
                    used.append(str(chosen))
    elif path.is_file():
        _merge(data, _read_toml(path))
        used.append(str(path))
    else:
        raise ConfigError(f"Configuration path '{path}' does not exist.")

    return _build(data, used)


def load_mapping(data: Mapping[str, Any]) -> PrimeeConfig:
    """Build a configuration from an in-memory mapping (used by tests)."""
    return _build(dict(data), [])


def _merge(target: dict, incoming: Mapping[str, Any]) -> None:
    for key, value in incoming.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


def _default_connectors() -> dict[str, ConnectorConfig]:
    return {kind: ConnectorConfig(kind=kind) for kind in ("metrics", "email", "calendar", "trends")}


def _build(data: Mapping[str, Any], used: list[str]) -> PrimeeConfig:
    vault_raw = _section(data, "vault")
    configured_root = str(vault_raw["root"]).strip() if vault_raw.get("root") else ""
    # An explicit setting always wins; the environment variable is the fallback
    # so a machine can supply its own path without editing a committed file.
    root = configured_root or os.environ.get(VAULT_PATH_ENV, "").strip()
    vault = VaultConfig(
        root=root or None,
        max_file_bytes=_positive_int(vault_raw.get("max_file_bytes"), 512 * 1024, "vault.max_file_bytes"),
    )

    runtime_raw = _section(data, "runtime")
    runtime = RuntimeConfig(
        dry_run=bool(runtime_raw.get("dry_run", False)),
        skills_dir=(str(runtime_raw["skills_dir"]).strip() or None) if runtime_raw.get("skills_dir") else None,
        state_dir=(str(runtime_raw["state_dir"]).strip() or None) if runtime_raw.get("state_dir") else None,
        min_route_score=_ratio(runtime_raw.get("min_route_score"), 0.35, "runtime.min_route_score"),
        ambiguity_margin=_ratio(runtime_raw.get("ambiguity_margin"), 0.15, "runtime.ambiguity_margin"),
    )

    schedule_raw = _section(data, "schedule")
    schedule = ScheduleConfig(
        timezone_mode=str(schedule_raw.get("timezone", "system")).strip().lower() or "system",
        trends_at=_time_of_day(schedule_raw.get("trends_at"), "07:30", "schedule.trends_at"),
        inbox_at=_time_of_day(schedule_raw.get("inbox_at"), "08:00", "schedule.inbox_at"),
        plan_at=_time_of_day(schedule_raw.get("plan_at"), "08:00", "schedule.plan_at"),
        run_every_day=bool(schedule_raw.get("run_every_day", True)),
        rest_days=_weekdays(schedule_raw.get("rest_days")),
        missed_job_policy=str(schedule_raw.get("missed_job_policy", "propose_once")).strip().lower(),
    )

    audit_raw = _section(data, "audit")
    audit = AuditConfig(
        enabled=bool(audit_raw.get("enabled", True)),
        log_message_bodies=bool(audit_raw.get("log_message_bodies", False)),
        path=(str(audit_raw["path"]).strip() or None) if audit_raw.get("path") else None,
    )

    policy = PermissionPolicy.from_mapping(_section(data, "permissions"))

    connectors = _default_connectors()
    for kind, raw in _section(data, "connectors").items():
        if not isinstance(raw, Mapping):
            raise ConfigError(f"Section [connectors.{kind}] must be a table.")
        options = {
            key: value
            for key, value in raw.items()
            if key not in ("provider", "credential_env")
        }
        connectors[str(kind)] = ConnectorConfig(
            kind=str(kind),
            provider=str(raw.get("provider", "none")).strip().lower() or "none",
            options=options,
            credential_env=str(raw.get("credential_env", "")).strip(),
        )

    metrics_raw = _section(data, "metrics")
    series_raw = metrics_raw.get("series", [])
    if not isinstance(series_raw, list):
        raise ConfigError("Section [[metrics.series]] must be a list of tables.")
    series: list[MetricSeries] = []
    for entry in series_raw:
        if not isinstance(entry, Mapping) or not str(entry.get("key", "")).strip():
            raise ConfigError("Every [[metrics.series]] entry needs a 'key'.")
        summaries = entry.get("summaries", ["latest"])
        if not isinstance(summaries, list) or not summaries:
            raise ConfigError(
                f"Metric series '{entry.get('key')}' needs a non-empty 'summaries' list."
            )
        series.append(
            MetricSeries(
                key=str(entry["key"]).strip(),
                label=str(entry.get("label", entry["key"])).strip(),
                unit=str(entry.get("unit", "")).strip(),
                summaries=tuple(str(item).strip().lower() for item in summaries),
            )
        )

    trends_raw = _section(data, "trends")
    sources = trends_raw.get("sources", [])
    if not isinstance(sources, list):
        raise ConfigError("Setting 'trends.sources' must be a list of source identifiers.")

    return PrimeeConfig(
        vault=vault,
        runtime=runtime,
        schedule=schedule,
        audit=audit,
        permissions=policy,
        connectors=connectors,
        metrics_series=tuple(series),
        trends_sources=tuple(str(item).strip() for item in sources if str(item).strip()),
        source_files=tuple(used),
    )


def _section(data: Mapping[str, Any], name: str) -> dict:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"Section [{name}] must be a table.")
    return dict(value)


def _positive_int(value: Any, default: int, name: str) -> int:
    if value is None:
        return default
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"Setting '{name}' must be a positive integer.")
    return value


def _ratio(value: Any, default: float, name: str) -> float:
    if value is None:
        return default
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ConfigError(f"Setting '{name}' must be a number between 0 and 1.")
    number = float(value)
    if not 0.0 < number <= 1.0:
        raise ConfigError(f"Setting '{name}' must be between 0 and 1.")
    return number


def _time_of_day(value: Any, default: str, name: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    if not _TIME_RE.match(text):
        raise ConfigError(f"Setting '{name}' must be a 24-hour time such as '08:00'.")
    return text


def _weekdays(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError("Setting 'schedule.rest_days' must be a list of weekday names.")
    days = []
    for item in value:
        name = str(item).strip().lower()
        if name not in WEEKDAY_NAMES:
            raise ConfigError(f"Unknown weekday '{item}' in 'schedule.rest_days'.")
        days.append(name)
    return tuple(days)
