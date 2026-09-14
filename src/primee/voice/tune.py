"""Listening comparison: the same sentence, a few synthesis settings.

The person reported the first Haaniye result as intelligible but choppy.
Two things Primee controls can change that: synthesising the whole text at
once instead of sentence by sentence, and the VITS sampling parameters. This
module writes one WAV per variant so the person can pick by ear and copy the
chosen values into ``voice.local.toml``. Primee does not pick for them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..core.clock import Clock, timestamp_iso
from ..core.errors import ErrorCode, VoiceError
from .benchmark import check_output_dir
from .service import VoiceService

TUNE_SENTENCE = "برنامه امروز شما آماده است. جلسه در ساعت هشت و سی دقیقه برگزار می‌شود. آیا می‌خواهید گزارش را بشنوید؟"

#: (key, description, parameters). Kept small: a listener can hold five in mind.
TUNE_VARIANTS: tuple[tuple[str, str, dict], ...] = (
    ("01-engine-default-per-sentence", "engine defaults, sentence by sentence (what the first run used)", {"speed": 1.0, "noise_scale": 0.667, "noise_scale_w": 0.8, "sentence_batch": 1}),
    ("02-whole-text", "engine defaults, whole text at once", {"speed": 1.0, "noise_scale": 0.667, "noise_scale_w": 0.8, "sentence_batch": 0}),
    ("03-whole-text-steadier", "less sampling noise", {"speed": 1.0, "noise_scale": 0.5, "noise_scale_w": 0.6, "sentence_batch": 0}),
    ("04-whole-text-steadier-slower", "less noise, ten percent slower", {"speed": 0.9, "noise_scale": 0.5, "noise_scale_w": 0.6, "sentence_batch": 0}),
    ("05-whole-text-minimal-noise", "near-deterministic sampling", {"speed": 1.0, "noise_scale": 0.333, "noise_scale_w": 0.4, "sentence_batch": 0}),
)


@dataclass(frozen=True)
class TuneReport:
    ok: bool
    output_dir: Optional[Path]
    report_path: Optional[Path]
    variants: tuple[dict, ...]
    reason: Optional[str] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output_dir": str(self.output_dir) if self.output_dir else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "reason": self.reason,
            "error_code": self.error_code,
            "variants": list(self.variants),
        }


def run_tuning(service: VoiceService, output_dir: Path, *, clock: Clock, approved: bool = False, text: str = TUNE_SENTENCE) -> TuneReport:
    try:
        output = check_output_dir(output_dir)
    except VoiceError as exc:
        return TuneReport(False, None, None, (), exc.sanitized_message(), exc.code)
    availability = service.adapter().availability()
    if not availability.available:
        reason = availability.reasons[0] if availability.reasons else "Voice engine unavailable."
        return TuneReport(False, None, None, (), reason, availability.error_code or ErrorCode.VOICE_RUNTIME_MISSING)

    stamp = timestamp_iso(clock).replace(":", "").replace("+", "p")
    run_dir = output / f"haaniye-tune-{stamp[:15]}"
    run_dir.mkdir(parents=True, exist_ok=False)

    variants: list[dict] = []
    for key, description, parameters in TUNE_VARIANTS:
        outcome = service.speak(text, keep_output=run_dir / key, play=False, approved=approved, parameters=parameters)
        wav_name: Optional[str] = None
        if outcome.synthesized and outcome.result is not None:
            final = run_dir / f"{key}.wav"
            try:
                outcome.result.output_path.replace(final)
                wav_name = final.name
                _remove_empty(run_dir / key)
            except OSError:
                wav_name = str(outcome.result.output_path)
        variants.append(
            {
                "key": key,
                "description": description,
                "parameters": parameters,
                "file": wav_name,
                "ok": outcome.synthesized,
                "error_code": outcome.error_code,
                "fallback_reason": outcome.fallback_reason,
                "metrics": outcome.result.describe() if outcome.result else {},
                "listening": {"fluent": None, "words_correct": None, "preferred": None},
            }
        )

    report_path = run_dir / "tune.json"
    payload = {
        "schema": "primee-voice-tune",
        "schema_version": 1,
        "generated_at": timestamp_iso(clock),
        "engine": service.adapter().name,
        "profile": service.profile.key if service.profile else None,
        "sentence": text,
        "variants": variants,
        "how_to_use": (
            "Listen to every file. Pick the variant that sounds most fluent and correct, then copy "
            "its parameters into config/voice.local.toml under [voice]: speed, noise_scale, "
            "noise_scale_w, sentence_batch. Primee does not choose for you."
        ),
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return TuneReport(ok=all(v["ok"] for v in variants), output_dir=run_dir, report_path=report_path, variants=tuple(variants))


def _remove_empty(path: Path) -> None:
    try:
        Path(path).rmdir()
    except OSError:
        pass
