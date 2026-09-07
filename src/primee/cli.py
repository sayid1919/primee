"""Primee's local command line interface.

    python -m primee list-skills
    python -m primee explain "what changed this week?"
    python -m primee run "morning brief"
    python -m primee run --skill plan --input day=2026-08-27 --dry-run
    python -m primee doctor
    python -m primee voice status

Nothing here contacts the network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .connectors.registry import ConnectorRegistry
from .core.approval import DenyAllApprovalGate, PreApprovedGate
from .core.audit import AuditLog, JsonlSink, MemorySink, NullSink
from .core.clock import SystemClock
from .core.config import PrimeeConfig, load_config
from .core.errors import PrimeeError
from .core.permissions import PERMISSIONS
from .core.runtime import PrimeeRuntime, bundled_skills_dir
from .core.skill_loader import discover_skills

DEFAULT_CONFIG_DIR = "config"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="primee", description="Primee - local-first personal operating system."
    )
    parser.add_argument("--version", action="version", version=f"Primee {__version__}")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to a configuration file or directory (default: ./config if present).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine readable JSON instead of text."
    )
    # The global options are also accepted after the subcommand, which is what
    # most people type. SUPPRESS keeps the subcommand copy from overwriting a
    # value that was already given before the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS
    )

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "list-skills",
        parents=[common],
        help="List every discovered skill and any load errors.",
    )

    explain = sub.add_parser(
        "explain",
        parents=[common],
        help="Show how a request would be routed, without running it.",
    )
    explain.add_argument("request", help="The request text.")

    run = sub.add_parser("run", parents=[common], help="Route and run a request.")
    run.add_argument("request", nargs="?", default="", help="The request text.")
    run.add_argument("--skill", default=None, help="Bypass routing and call this skill directly.")
    run.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Handler input. Repeatable. Values are parsed as JSON when possible.",
    )
    run.add_argument("--dry-run", action="store_true", help="Plan side effects but perform none.")
    run.add_argument(
        "--approve",
        action="append",
        default=[],
        metavar="PERMISSION",
        help="Pre-approve one permission for this run only. Repeatable.",
    )
    run.add_argument("--no-audit", action="store_true", help="Do not write to the audit log file.")

    vault = sub.add_parser(
        "vault",
        parents=[common],
        help="Run one Vault memory operation through the permission layer.",
    )
    vault.add_argument("operation", help="init, validate, read, search_text, rebuild_index, ...")
    vault.add_argument("--path", default=None, help="Vault-relative path.")
    vault.add_argument("--target", default=None, help="Wikilink target, e.g. wiki/topic.")
    vault.add_argument("--root", default=None, help="Approved Vault path (init only).")
    vault.add_argument("--query", default=None, help="Search text.")
    vault.add_argument("--tag", default=None, help="Tag to search for.")
    vault.add_argument("--field", default=None, help="Frontmatter field for a metadata search.")
    vault.add_argument("--value", default=None, help="Value for a metadata search.")
    vault.add_argument("--limit", type=int, default=None, help="Maximum results.")
    vault.add_argument("--prefix", default=None, help="Folder prefix for list.")
    vault.add_argument("--adopt", action="store_true", help="Adopt a non-empty directory (init).")
    vault.add_argument("--dry-run", action="store_true", help="Plan the operation, perform none.")
    vault.add_argument(
        "--approve", action="append", default=[], metavar="PERMISSION",
        help="Pre-approve one permission for this run only. Repeatable.",
    )
    vault.add_argument("--no-audit", action="store_true", help="Do not write to the audit log file.")

    sub.add_parser(
        "doctor",
        parents=[common],
        help="Show configuration, connector status and the permission policy.",
    )
    sub.add_parser(
        "permissions", parents=[common], help="List every permission Primee understands."
    )

    voice = sub.add_parser(
        "voice",
        parents=[common],
        help="Local voice output (Step Three): status, profile, speak, benchmark.",
    )
    voice_sub = voice.add_subparsers(dest="voice_command", required=False)
    voice_sub.add_parser("status", parents=[common], help="Show whether speech is possible right now.")
    voice_sub.add_parser("profile", parents=[common], help="Show the voice profile, its licences and warnings.")
    speak = voice_sub.add_parser("speak", parents=[common], help="Speak one short text locally, or show it if speech is unavailable.")
    speak.add_argument("text", help="The text. Only a short summary of it is spoken.")
    speak.add_argument(
        "--approve", action="append", default=[], metavar="PERMISSION",
        help="Pre-approve audio.playback for this run only.",
    )
    speak.add_argument("--no-audit", action="store_true", help="Do not write to the audit log file.")
    bench = voice_sub.add_parser(
        "benchmark", parents=[common],
        help="Synthesise the fixed Persian benchmark set into WAV files outside the repository. Plays nothing.",
    )
    bench.add_argument("--output", required=True, help="Absolute directory OUTSIDE the repository for the WAV files and report.")
    bench.add_argument(
        "--approve", action="append", default=[], metavar="PERMISSION",
        help="Pre-approve audio.playback for this run only.",
    )
    bench.add_argument("--no-audit", action="store_true", help="Do not write to the audit log file.")
    return parser


def resolve_config(explicit: Optional[str]) -> PrimeeConfig:
    if explicit:
        return load_config(explicit)
    default = Path.cwd() / DEFAULT_CONFIG_DIR
    if default.is_dir():
        return load_config(default)
    return load_config(None)


def parse_inputs(pairs: list[str]) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for pair in pairs:
        key, separator, value = str(pair).partition("=")
        if not separator or not key.strip():
            raise PrimeeError(
                "INVALID_INPUT", f"Input '{pair}' must be written as KEY=VALUE."
            )
        try:
            inputs[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            inputs[key.strip()] = value
    return inputs


def build_runtime(config: PrimeeConfig, args: argparse.Namespace) -> PrimeeRuntime:
    clock = SystemClock()
    dry_run = bool(getattr(args, "dry_run", False)) or config.runtime.dry_run

    if not config.audit.enabled:
        sink = NullSink()
    elif getattr(args, "no_audit", False) or dry_run:
        sink = MemorySink()
    else:
        sink = JsonlSink(config.audit_path())
    audit = AuditLog(sink, clock, enabled=config.audit.enabled)

    approvals = getattr(args, "approve", []) or []
    gate = PreApprovedGate(approvals) if approvals else DenyAllApprovalGate()

    return PrimeeRuntime(
        config,
        approval_gate=gate,
        audit=audit,
        clock=clock,
        connectors=ConnectorRegistry.from_config(config),
        dry_run=dry_run,
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return _dispatch(parser, args)
    except BrokenPipeError:
        # `primee doctor | head` closes the pipe early. Send the rest of our
        # output to the void so Python does not print a traceback at shutdown.
        _silence_stdout()
        return 0


def _dispatch(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        config = resolve_config(getattr(args, "config", None))
    except PrimeeError as exc:
        print(f"Configuration error: {exc.sanitized_message()}", file=sys.stderr)
        return 2

    if args.command == "permissions":
        return _permissions(args)
    if args.command == "list-skills":
        return _list_skills(config, args)
    if args.command == "doctor":
        return _doctor(config, args)
    if args.command == "explain":
        return _explain(config, args)
    if args.command == "run":
        return _run(config, args)
    if args.command == "vault":
        return _vault(config, args)
    if args.command == "voice":
        return _voice(config, args)
    parser.error(f"Unknown command {args.command!r}")
    return 2


def _silence_stdout() -> None:
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    except OSError:  # pragma: no cover - defensive
        pass


def _skills_root(config: PrimeeConfig) -> Path:
    from .core.config import expand

    if config.runtime.skills_dir:
        return Path(expand(config.runtime.skills_dir))
    return bundled_skills_dir()


def _permissions(args: argparse.Namespace) -> int:
    rows = [
        {
            "name": spec.name,
            "description": spec.description,
            "implemented": spec.implemented,
            "skill_requestable": spec.skill_requestable,
        }
        for spec in sorted(PERMISSIONS.values(), key=lambda s: s.name)
    ]
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print("Primee permissions:")
    for row in rows:
        state = "implemented" if row["implemented"] else "reserved for a later step"
        print(f"  {row['name']:<26} {state:<28} {row['description']}")
    return 0


def _list_skills(config: PrimeeConfig, args: argparse.Namespace) -> int:
    registry = discover_skills(_skills_root(config))
    payload = {
        "root": str(_skills_root(config)),
        "skills": [skill.manifest.describe() for skill in registry],
        "errors": [error.to_dict() for error in registry.errors],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1 if registry.errors else 0
    print(f"Skills root: {payload['root']}")
    for skill in registry:
        manifest = skill.manifest
        print(f"\n  {manifest.name} v{manifest.version}")
        print(f"    {manifest.description}")
        print(f"    triggers   : {', '.join(manifest.triggers)}")
        print(f"    exclusions : {', '.join(manifest.exclusions) or '(none)'}")
        print(f"    permissions: {', '.join(sorted(manifest.required_permissions)) or '(none)'}")
        print(f"    handler    : {manifest.handler}")
    if registry.errors:
        print("\nLoad errors:")
        for error in registry.errors:
            print(f"  [{error.error_code}] {error.directory}: {error.message}")
        return 1
    return 0


def _doctor(config: PrimeeConfig, args: argparse.Namespace) -> int:
    registry = discover_skills(_skills_root(config))
    connectors = ConnectorRegistry.from_config(config)
    vault_state = "not configured"
    if config.vault.configured:
        try:
            from .skills.vault.storage import VaultStorage

            VaultStorage(config.vault.root, max_file_bytes=config.vault.max_file_bytes)
            vault_state = "ready"
        except PrimeeError as exc:
            vault_state = f"error: {exc.sanitized_message()}"

    from .voice.service import VoiceService

    voice_status = VoiceService(config).status()
    payload = {
        "version": __version__,
        "config": config.describe(),
        "vault": vault_state,
        "skills": registry.names(),
        "skill_errors": [error.to_dict() for error in registry.errors],
        "connectors": connectors.describe(),
        "permission_policy": config.permissions.describe(),
        "audit_path": str(config.audit_path()),
        "voice": voice_status,
        "hosted_ai_dependencies": [],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    print(f"Primee {__version__}")
    print(f"  config files : {', '.join(config.source_files) or '(built-in defaults)'}")
    print(f"  skills       : {', '.join(payload['skills']) or '(none)'}")
    print(f"  vault        : {vault_state}")
    print(f"  audit log    : {payload['audit_path']} (enabled={config.audit.enabled})")
    print(f"  dry run      : {config.runtime.dry_run}")
    print("  connectors   :")
    for kind, info in payload["connectors"].items():
        print(f"    {kind:<9} provider={info['provider']:<8} configured={info['configured']}")
    print("  permissions  :")
    for name, mode in payload["permission_policy"]["modes"].items():
        print(f"    {name:<26} {mode}")
    adapter = voice_status["adapter"]
    print(
        f"  voice        : enabled={voice_status['enabled']} engine={voice_status['tts_engine']} "
        f"available={adapter['available']} permission={voice_status['permission']['mode']}"
    )
    for reason in adapter["reasons"]:
        print(f"    - {reason}")
    if registry.errors:
        print("  skill errors :")
        for error in registry.errors:
            print(f"    [{error.error_code}] {error.directory}: {error.message}")
    return 0


def _explain(config: PrimeeConfig, args: argparse.Namespace) -> int:
    runtime = PrimeeRuntime(config, clock=SystemClock())
    decision = runtime.explain(args.request)
    if getattr(args, "json", False):
        print(json.dumps(decision.to_dict(), ensure_ascii=False, indent=2))
        return 0 if decision.matched else 1
    print(f"Request : {args.request}")
    print(f"Status  : {decision.status}")
    print(f"Skill   : {decision.skill_name or '(none)'}")
    print(f"Why     : {decision.explanation}")
    print("Scores  :")
    for score in decision.all_scores:
        flag = " (vetoed by exclusion)" if score.vetoed else ""
        phrases = ", ".join(f"{p}={v:.2f}" for p, v in score.matched_triggers[:3])
        print(f"  {score.skill_name:<10} {score.score:.2f}{flag}  {phrases}")
    return 0 if decision.matched else 1


def _vault(config: PrimeeConfig, args: argparse.Namespace) -> int:
    """Run one Vault operation through the same gate every skill uses."""
    inputs: dict[str, Any] = {"operation": args.operation, "requested_by": "cli"}
    for name in ("path", "target", "root", "query", "tag", "field", "value", "prefix"):
        value = getattr(args, name, None)
        if value is not None:
            inputs[name] = value
    if args.limit is not None:
        inputs["limit"] = args.limit
    if args.adopt:
        inputs["adopt_non_empty"] = True

    runtime = build_runtime(config, args)
    outcome = runtime.handle("", inputs=inputs, skill_name="vault")

    if getattr(args, "json", False):
        print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 1

    result = outcome.result
    if result is None:
        print(f"Vault failed: {outcome.message}", file=sys.stderr)
        return 1
    if not result.success:
        print(f"[{result.error_code}] {result.sanitized_error_message}", file=sys.stderr)
        _print_vault_details(result.structured_data)
        return 1
    print(result.summary)
    _print_vault_details(result.structured_data)
    return 0


def _print_vault_details(data: dict) -> None:
    for hit in data.get("results", [])[:20]:
        print(f"  {hit['updated'][:19]}  {hit['type']:<16} {hit['path']}")
        print(f"      {hit['title']} — {hit['summary']}")
    for link in data.get("backlinks", []):
        print(f"  <- {link}")
    for entry in data.get("dangling_links", []) or data.get("dangling", []):
        print(f"  dangling: [[{entry['target']}]] in {entry['source']}")
    for entry in data.get("ambiguous_links", []) or data.get("ambiguous", []):
        print(f"  ambiguous: [[{entry['target']}]] in {entry['source']} "
              f"-> {', '.join(entry['candidates'])}")
    for page in data.get("broken_pages", []):
        print(f"  broken: {page['path']}: {page['error']}")
    for page_id, paths in (data.get("duplicate_ids") or {}).items():
        print(f"  duplicate id {page_id}: {', '.join(paths)}")
    if "will_create_directories" in data:
        # After a real run these lists describe what was actually created.
        dry = data.get("dry_run", False)
        verb = "would create" if dry else "created"
        for folder in (
            data["will_create_directories"] if dry else data.get("created_directories", [])
        ):
            print(f"  {verb} folder: {folder}")
        for name in data["will_create_files"] if dry else data.get("created_files", []):
            print(f"  {verb} file: {name}")


def _voice_service(config: PrimeeConfig, args: argparse.Namespace):
    from .voice.service import VoiceService

    clock = SystemClock()
    if not config.audit.enabled:
        sink = NullSink()
    elif getattr(args, "no_audit", False):
        sink = MemorySink()
    else:
        sink = JsonlSink(config.audit_path())
    return VoiceService(config, audit=AuditLog(sink, clock, enabled=config.audit.enabled), clock=clock)


def _voice(config: PrimeeConfig, args: argparse.Namespace) -> int:
    """Step Three entry point. Only the text-to-speech benchmark path exists yet."""
    from .voice import VOICE_STATUS
    from .voice.service import PLAYBACK_PERMISSION

    as_json = getattr(args, "json", False)
    command = getattr(args, "voice_command", None)
    service = _voice_service(config, args)

    if command in (None, "status"):
        status = service.status()
        if command is None:
            status["notice"] = (
                "The push-to-talk voice loop is not implemented yet. "
                "Available: `primee voice status`, `primee voice profile`, "
                "`primee voice speak TEXT`, `primee voice benchmark --output DIR`."
            )
            status["stage"] = VOICE_STATUS
        if as_json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 0
        if command is None:
            print(status["notice"])
            print()
        adapter = status["adapter"]
        print(f"Voice enabled   : {status['enabled']}  (engine {status['tts_engine']}, profile {config.voice.profile})")
        print(f"Permission      : {status['permission']['name']} = {status['permission']['mode']}")
        print(f"Player          : {status['player']['name']} (available={status['player']['available']})")
        print(f"Engine ready    : {adapter['available']}")
        for reason in adapter["reasons"]:
            print(f"  - {reason}")
        print(f"Models directory: {status['models_dir']}")
        print(f"Implemented     : {status['implemented']}")
        return 0

    if command == "profile":
        profile = service.profile
        if profile is None:
            print(f"Unknown voice profile {config.voice.profile!r}.", file=sys.stderr)
            return 1
        if as_json:
            print(json.dumps(profile.describe(), ensure_ascii=False, indent=2))
            return 0
        print(f"{profile.display_name}  [{profile.key}]")
        print(f"  language : {profile.language}   engine: {profile.engine}   model: {profile.model_type}   quality: {profile.quality}")
        print(f"  original : {profile.original_repository}/{profile.original_path}")
        print(f"  converted: {profile.model_repository}")
        print("  licences :")
        for record in profile.licenses:
            print(f"    {record.subject:<28} {record.license or 'not stated':<12} {record.statement}")
        print(f"  provenance SOURCE file: {profile.provenance_source_file}  -> {profile.provenance_note}")
        print(f"  speaker gender        : {profile.speaker_gender}")
        print(f"  redistribution        : {profile.redistribution_status}")
        print(f"  approved use          : {profile.approved_use}")
        for warning in profile.warnings:
            print(f"  warning: {warning}")
        return 0

    approved = PLAYBACK_PERMISSION in (getattr(args, "approve", []) or [])

    if command == "speak":
        outcome = service.speak(args.text, approved=approved)
        if as_json:
            payload = outcome.describe()
            payload["text"] = outcome.text
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0 if outcome.spoken else 1
        print(outcome.text)
        if outcome.spoken:
            print(f"  (spoken with {outcome.engine})")
        else:
            print(f"  (text only: {outcome.fallback_reason})")
        for warning in outcome.warnings:
            print(f"  warning: {warning}")
        return 0 if outcome.spoken else 1

    if command == "benchmark":
        from .voice.benchmark import run_benchmark

        report = run_benchmark(service, Path(args.output), clock=service.clock, approved=approved)
        if as_json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            return 0 if report.ok else 1
        if not report.ok and not report.cases:
            print(f"Benchmark did not run: {report.reason}", file=sys.stderr)
            return 1
        print(f"Benchmark files: {report.output_dir}")
        for case in report.cases:
            if case.ok:
                metrics = case.metrics
                print(
                    f"  {case.key:<16} {case.file:<20} audio {metrics.get('audio_seconds')}s "
                    f"synthesis {metrics.get('synthesis_seconds')}s rtf {metrics.get('real_time_factor')}"
                )
            else:
                print(f"  {case.key:<16} FAILED [{case.error_code}] {case.fallback_reason}")
        print(f"Report: {report.report_path}")
        print("Listen to every file, then classify: Accept / Accept temporarily / Reject.")
        return 0 if report.ok else 1

    print(f"Unknown voice command {command!r}.", file=sys.stderr)
    return 2


def _run(config: PrimeeConfig, args: argparse.Namespace) -> int:
    try:
        inputs = parse_inputs(args.input)
    except PrimeeError as exc:
        print(f"Input error: {exc.sanitized_message()}", file=sys.stderr)
        return 2

    runtime = build_runtime(config, args)
    outcome = runtime.handle(args.request, inputs=inputs, skill_name=args.skill)

    if getattr(args, "json", False):
        print(json.dumps(outcome.to_dict(), ensure_ascii=False, indent=2))
        return 0 if outcome.ok else 1

    if outcome.dry_run:
        print("[dry run] No file was written and no external action was taken.\n")
    if outcome.status == "clarification_needed":
        print("Primee did not act, because it is not confident enough to guess.")
        print(f"  {outcome.message}")
        if outcome.route.candidates:
            print("  Candidate skills:")
            for candidate in outcome.route.candidates:
                print(f"    - {candidate.skill_name} (score {candidate.score:.2f})")
        return 1

    result = outcome.result
    if result is None:
        print(f"Primee failed: {outcome.message}")
        return 1

    print(result.summary if result.success else f"[{result.error_code}] {result.sanitized_error_message}")
    for warning in result.warnings:
        print(f"  warning: {warning}")
    for write in outcome.vault_writes:
        state = "written" if write.performed else f"not written ({write.state})"
        print(f"  vault {write.operation} {write.path}: {state} - {write.reason}")
    for action in outcome.pending_external_actions:
        print(f"  pending approval: {action['action']} -> {action['target']}")
    return 0 if outcome.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
