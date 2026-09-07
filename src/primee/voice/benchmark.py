"""The fixed Haaniye benchmark.

Seven Persian phrases, always the same, synthesised into WAV files in a folder
the person chose *outside* the repository, plus one JSON report with the
measured numbers. Whether the voice is acceptable is decided by listening, so
the report leaves those fields empty for the person to fill in: Primee never
declares a voice intelligible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..core.clock import Clock, timestamp_iso
from ..core.errors import ErrorCode, VoiceError
from .service import SpeechOutcome, VoiceService

#: (key, what it exercises, text). The keys are stable and used as file names.
BENCHMARK_PHRASES: tuple[tuple[str, str, str], ...] = (
    ("01-greeting", "short greeting", "سلام، من پرایمی هستم. چطور می‌توانم کمکتان کنم؟"),
    ("02-confirmation", "normal Primee confirmation", "برنامه امروز شما آماده است."),
    ("03-date-time", "a date and a time", "جلسه در ساعت هشت و سی دقیقه روز بیست و پنجم سپتامبر برگزار می‌شود."),
    ("04-numbers", "several numbers", "مقدار فروش امروز یک هزار و دویست و پنجاه فرانک است."),
    ("05-business", "a business-related sentence", "گزارش وب‌سایت و تقویم شما بررسی شد."),
    ("06-mixed", "Persian with an English technical term", "لطفاً وضعیت website metrics را نمایش بده."),
    ("07-question", "punctuation and a question", "آیا می‌خواهید این عملیات اجرا شود؟"),
)

LISTENING_FIELDS = (
    "intelligible",
    "numbers_pronounced_correctly",
    "mixed_language_behaviour",
    "perceived_gender",
    "classification",  # Accept | Accept temporarily | Reject
)


@dataclass(frozen=True)
class BenchmarkCase:
    key: str
    exercises: str
    characters: int
    file: Optional[str]
    ok: bool
    error_code: Optional[str]
    fallback_reason: Optional[str]
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "exercises": self.exercises,
            "characters": self.characters,
            "file": self.file,
            "ok": self.ok,
            "error_code": self.error_code,
            "fallback_reason": self.fallback_reason,
            "metrics": self.metrics,
            "listening": {name: None for name in LISTENING_FIELDS},
        }


@dataclass(frozen=True)
class BenchmarkReport:
    ok: bool
    output_dir: Optional[Path]
    report_path: Optional[Path]
    cases: tuple[BenchmarkCase, ...]
    reason: Optional[str] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "reason": self.reason,
            "error_code": self.error_code,
            "cases": [case.to_dict() for case in self.cases],
        }


def repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def check_output_dir(output_dir: Path) -> Path:
    """Benchmark audio must never land inside the repository."""
    output = Path(output_dir)
    if not output.is_absolute():
        raise VoiceError(ErrorCode.INVALID_INPUT, "The benchmark output directory must be an absolute path.")
    resolved = output.resolve() if output.exists() else output.parent.resolve() / output.name
    try:
        resolved.relative_to(repository_root())
    except ValueError:
        return output
    raise VoiceError(
        ErrorCode.INVALID_INPUT,
        "The benchmark output directory must be outside the Primee repository.",
    )


def run_benchmark(service: VoiceService, output_dir: Path, *, clock: Clock, approved: bool = False) -> BenchmarkReport:
    """Synthesise every phrase to a file. Plays nothing. Deletes nothing."""
    try:
        output = check_output_dir(output_dir)
    except VoiceError as exc:
        return BenchmarkReport(False, None, None, (), exc.sanitized_message(), exc.code)

    availability = service.adapter().availability()
    if not availability.available:
        reason = availability.reasons[0] if availability.reasons else "Voice engine unavailable."
        return BenchmarkReport(False, None, None, (), reason, availability.error_code or ErrorCode.VOICE_RUNTIME_MISSING)

    stamp = timestamp_iso(clock).replace(":", "").replace("+", "p")
    run_dir = output / f"haaniye-benchmark-{stamp[:15]}"
    run_dir.mkdir(parents=True, exist_ok=False)

    cases: list[BenchmarkCase] = []
    for key, exercises, text in BENCHMARK_PHRASES:
        case_dir = run_dir / key
        outcome: SpeechOutcome = service.speak(text, keep_output=case_dir, play=False, approved=approved)
        wav_name: Optional[str] = None
        if outcome.synthesized and outcome.result is not None:
            final = run_dir / f"{key}.wav"
            try:
                outcome.result.output_path.replace(final)
                wav_name = final.name
                _remove_empty_dirs(case_dir)
            except OSError:
                wav_name = str(outcome.result.output_path.relative_to(run_dir))
        cases.append(
            BenchmarkCase(
                key=key,
                exercises=exercises,
                characters=len(text),
                file=wav_name,
                ok=outcome.synthesized,
                error_code=outcome.error_code,
                fallback_reason=outcome.fallback_reason,
                metrics=outcome.result.describe() if outcome.result else {},
            )
        )

    report_path = run_dir / "benchmark.json"
    payload = {
        "schema": "primee-voice-benchmark",
        "schema_version": 1,
        "generated_at": timestamp_iso(clock),
        "profile": service.profile.describe() if service.profile else None,
        "engine": service.adapter().name,
        "phrases": [{"key": k, "exercises": e, "text": t} for k, e, t in BENCHMARK_PHRASES],
        "cases": [case.to_dict() for case in cases],
        "how_to_judge": (
            "Listen to every file. Fill in the 'listening' fields per case, then choose one "
            "overall classification: Accept, Accept temporarily, or Reject. Primee does not "
            "decide this for you."
        ),
        "warnings": list(service.profile.provenance_warnings()) if service.profile else [],
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return BenchmarkReport(
        ok=all(case.ok for case in cases),
        output_dir=run_dir,
        report_path=report_path,
        cases=tuple(cases),
    )


def _remove_empty_dirs(path: Path) -> None:
    current = Path(path)
    for _ in range(3):
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
