"""Voice profiles: the facts Primee records about a speech model.

Every value here was read from a primary source and is quoted, not inferred.
Where a source is silent (speaker gender, dataset provenance) the profile says
so instead of guessing, and the warnings are surfaced every time the profile is
used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class LicenseRecord:
    """One licence statement, tied to exactly one thing and one source."""

    subject: str
    license: Optional[str]
    source_url: str
    statement: str
    verified_how: str


@dataclass(frozen=True)
class VoiceProfile:
    key: str
    display_name: str
    language: str
    engine: str
    model_type: str
    quality: str
    speakers: int
    model_repository: str
    model_files: tuple[str, ...]
    data_dir: str
    original_repository: str
    original_path: str
    licenses: tuple[LicenseRecord, ...]
    provenance_source_file: str
    provenance_note: str
    speaker_gender: str
    redistribution_status: str
    approved_use: str
    warnings: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)
    listening_result: Mapping[str, str] = field(default_factory=dict)

    def license_for(self, subject: str) -> Optional[LicenseRecord]:
        for record in self.licenses:
            if record.subject == subject:
                return record
        return None

    def provenance_warnings(self) -> tuple[str, ...]:
        """The warnings that must accompany every use of this voice."""
        return self.warnings

    def describe(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "language": self.language,
            "engine": self.engine,
            "model_type": self.model_type,
            "quality": self.quality,
            "speakers": self.speakers,
            "model_repository": self.model_repository,
            "model_files": list(self.model_files),
            "data_dir": self.data_dir,
            "original_repository": self.original_repository,
            "original_path": self.original_path,
            "licenses": [
                {
                    "subject": r.subject,
                    "license": r.license,
                    "source_url": r.source_url,
                    "statement": r.statement,
                    "verified_how": r.verified_how,
                }
                for r in self.licenses
            ],
            "provenance_source_file": self.provenance_source_file,
            "provenance_note": self.provenance_note,
            "speaker_gender": self.speaker_gender,
            "redistribution_status": self.redistribution_status,
            "approved_use": self.approved_use,
            "warnings": list(self.warnings),
            "notes": list(self.notes),
            "listening_result": dict(self.listening_result),
        }


_MYCROFT_BASE = "https://github.com/MycroftAI/mimic3-voices/blob/master/voices/fa/haaniye_low"

HAANIYE = VoiceProfile(
    key="haaniye",
    display_name="Haaniye (Persian, low quality)",
    language="fa",
    engine="sherpa-onnx",
    model_type="vits-mimic3",
    quality="low",
    speakers=1,
    model_repository="csukuangfj/vits-mimic3-fa-haaniye_low",
    model_files=("fa-haaniye_low.onnx", "fa-haaniye_low.onnx.json", "tokens.txt"),
    data_dir="espeak-ng-data",
    original_repository="MycroftAI/mimic3-voices",
    original_path="voices/fa/haaniye_low",
    licenses=(
        LicenseRecord(
            subject="voice",
            license="CC0",
            source_url=f"{_MYCROFT_BASE}/LICENSE",
            statement="The LICENSE file contains exactly: CC-0",
            verified_how="read from the raw file on 2026-09-02",
        ),
        LicenseRecord(
            subject="dataset",
            license=None,
            source_url=f"{_MYCROFT_BASE}/README.md",
            statement=(
                "README describes the voice as 'based on a public domain dataset'; "
                "no licence file for the dataset itself exists"
            ),
            verified_how="read from the raw README on 2026-09-02",
        ),
        LicenseRecord(
            subject="converted-model-repository",
            license=None,
            source_url="https://huggingface.co/csukuangfj/vits-mimic3-fa-haaniye_low",
            statement=(
                "The converted repository declares no licence metadata. Primee does not "
                "assign one; the original voice licence and provenance documents are "
                "installed next to the model instead."
            ),
            verified_how="reported by the person; recorded, not independently fetched",
        ),
        LicenseRecord(
            subject="engine",
            license="Apache-2.0",
            source_url="https://github.com/k2-fsa/sherpa-onnx/blob/master/LICENSE",
            statement="sherpa-onnx and sherpa-onnx-core are Apache License 2.0",
            verified_how="read from the repository LICENSE and the PyPI metadata on 2026-09-02",
        ),
        LicenseRecord(
            subject="bundled-libraries",
            license=None,
            source_url="https://pypi.org/project/sherpa-onnx-core/",
            statement=(
                "sherpa-onnx-core bundles onnxruntime, espeak-ng data and other "
                "components under their own licences; they are not assessed here"
            ),
            verified_how="not assessed",
        ),
    ),
    provenance_source_file="TBD",
    provenance_note=(
        "The upstream SOURCE file contains only 'TBD'. The exact dataset provenance "
        "is therefore incomplete."
    ),
    speaker_gender="not stated in the official documentation; perceived as female by listening on 2026-09-14",
    redistribution_status="not assessed; no claim of redistribution or commercial clearance",
    approved_use="private local benchmark only",
    warnings=(
        "Dataset provenance is incomplete: the upstream SOURCE file says TBD.",
        "Low-quality model; intelligibility must be judged by listening.",
        "Speaker gender is not stated in the official documentation.",
        "Approved for a private local benchmark only; not cleared for redistribution.",
    ),
    notes=(
        "Original voice: MycroftAI/mimic3-voices voices/fa/haaniye_low (CC0).",
        "Converted for sherpa-onnx by csukuangfj; the conversion adds no licence metadata.",
        "The sherpa-onnx wheel only exposes a text2token CLI, so Primee runs its own worker script.",
    ),
    listening_result={
        "date": "2026-09-14",
        "classification": "accept temporarily",
        "intelligible": "yes",
        "perceived_gender": "female (by listening; not stated by the documentation)",
        "fluency": "choppy; sentence-by-sentence delivery and a low-quality model",
        "pronunciation": "some words wrong; the phonemiser guesses unwritten short vowels",
        "next": "listening comparison of synthesis settings (primee voice tune) and an editable pronunciation lexicon",
    },
)

PROFILES: dict[str, VoiceProfile] = {HAANIYE.key: HAANIYE}


def get_profile(key: str) -> Optional[VoiceProfile]:
    return PROFILES.get(str(key).strip().lower())
